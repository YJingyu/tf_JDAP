from demo.detectAPI import DetectAPI
from data_base import WIDER
from tools.utils import *

import numpy as np
import numpy.random as npr
import argparse
import os
import os.path as osp
import sys

os.environ.setdefault('CUDA_VISIBLE_DEVICES', '0')

# Set yourself fold root
net_size = 48
data_dir = '/home/dafu/data/jdap_data/%d' % net_size
add_dir_name = ''  # default is ''


def detect_save_pickle(detector, dataset_indicator, pickle_name):
    count = 0
    detections = list()  # detect result
    fin = open(pickle_name, 'w')
    if not osp.exists(pickle_name):
        raise Exception("pickle file not exist.")
    fin.close()

    for label_info in dataset_indicator.label_infos:
        # Interactive information
        if count % 100 == 0:
            print("Handle image %d " % count)
        count += 1
        image_name = dataset_indicator.get_image_name(label_info)
        image = cv2.imread(image_name)
        cal_boxes = detector.detect(image)
        detections.append(cal_boxes)

    with open(pickle_name, 'wb') as f:
        cPickle.dump(detections, f, cPickle.HIGHEST_PROTOCOL)


def save_hard_example(dataset_indicator, pickle_name):
    # load ground truth from annotation file
    # format of each line: image/path [x1,y1,x2,y2] for each gt_box in this image

    annotations = dataset_indicator.label_infos
    num_of_images = len(annotations)
    print ("processing %d images in total" % num_of_images)

    save_path = data_dir
    mode = dataset_indicator.mode
    neg_save_dir = os.path.join(data_dir, "%s_%snegative" % (mode, add_dir_name))
    pos_save_dir = os.path.join(data_dir, "%s_%spositive" % (mode, add_dir_name))
    part_save_dir = os.path.join(data_dir, "%s_%spart" % (mode, add_dir_name))
    for dir_path in [neg_save_dir, pos_save_dir, part_save_dir]:
        if not os.path.exists(dir_path):
            os.makedirs(dir_path)

    fpos = open(os.path.join(save_path, '%s_%spos_%d.txt' % (mode, add_dir_name, net_size)), 'w')
    fneg = open(os.path.join(save_path, '%s_%sneg_%d.txt' % (mode, add_dir_name, net_size)), 'w')
    fpart = open(os.path.join(save_path, '%s_%spart_%d.txt' % (mode, add_dir_name, net_size)), 'w')

    det_boxes = cPickle.load(open(pickle_name, 'r'))
    print (len(det_boxes), num_of_images)
    assert len(det_boxes) == num_of_images, "incorrect detections or ground truths"

    # index of neg, pos and part face, used as their image names
    n_idx = 0
    p_idx = 0
    d_idx = 0
    image_done = 0
    for label_info, dets in zip(annotations, det_boxes):
        if image_done % 100 == 0:
            print ("%d images done" % image_done)
        image_done += 1
        if dets.shape[0] == 0:
            continue
        # 'image_name' and 'gt_boxes'
        dict_info = dataset_indicator.label_parser(label_info)
        image_name = dict_info['image_name']
        # Get attribute ground truth
        gt_boxes = dict_info['gt_boxes']

        image = cv2.imread(image_name)
        img_h, img_w, _ = image.shape
        dets = convert_to_square(dets)
        dets[:, 0:4] = np.round(dets[:, 0:4])
        for box in dets:
            x_left, y_top, x_right, y_bottom, _ = box.astype(int)
            box_w = x_right - x_left + 1
            box_h = y_bottom - y_top + 1

            # ignore box that is too small or beyond image border
            if box_w < 20 or x_left < 0 or y_top < 0 or x_right > img_w - 1 or y_bottom > img_h - 1:
                continue

            # compute intersection over union(IoU) between current box and all gt boxes
            iou = IoU(box, gt_boxes)
            cropped_im = image[y_top:y_bottom + 1, x_left:x_right + 1, :]
            resized_im = cv2.resize(cropped_im, (net_size, net_size), interpolation=cv2.INTER_LINEAR)

            # save negative images and write label
            if np.max(iou) < 0.3:
                # Sampling select very hard samples
                if net_size == 24 or net_size == 18:
                    if npr.rand(1) > 0.3 or box[4] < 0.6:
                        continue
                save_file = os.path.join(neg_save_dir, "%d.jpg" % n_idx)
                fneg.write("%d/%s_%snegative/%d.jpg" %
                           (net_size, mode, add_dir_name, n_idx) + ' 0 0 0 0 0\n')
                cv2.imwrite(save_file, resized_im)
                n_idx += 1
            else:
                # find gt_box with the highest iou
                idx = np.argmax(iou)
                assigned_gt = gt_boxes[idx]
                x1, y1, x2, y2 = assigned_gt

                # compute bbox reg label
                offset_x1 = (x1 - x_left) / float(box_w)
                offset_y1 = (y1 - y_top) / float(box_h)
                offset_x2 = (x2 - x_right) / float(box_w)
                offset_y2 = (y2 - y_bottom) / float(box_h)

                # Save positive and part-face images and write labels
                if np.max(iou) >= 0.65:
                    save_file = os.path.join(pos_save_dir, "%d.jpg" % p_idx)
                    fpos.write("%d/%s_%spositive/%d.jpg" % (net_size, mode, add_dir_name, p_idx) +
                               ' 1 %.2f %.2f %.2f %.2f\n' % (offset_x1, offset_y1, offset_x2, offset_y2))
                    cv2.imwrite(save_file, resized_im)
                    p_idx += 1
                elif np.max(iou) >= 0.4:
                    save_file = os.path.join(part_save_dir, "%d.jpg" % d_idx)
                    fpart.write("%d/%s_%spart/%d.jpg" % (net_size, mode, add_dir_name, d_idx) +
                                ' -1 %.2f %.2f %.2f %.2f\n' % (offset_x1, offset_y1, offset_x2, offset_y2))
                    cv2.imwrite(save_file, resized_im)
                    d_idx += 1

    fpos.close()
    fneg.close()
    fpart.close()


def save_gt_sample(dataset_indicator):
    annotations = dataset_indicator.label_infos
    num_of_images = len(annotations)
    print ("processing %d images in total" % num_of_images)
    idx = 0
    p_idx = 0
    save_path = data_dir
    pos_save_dir = os.path.join(save_path, "train_gt_positive")
    if not os.path.exists(pos_save_dir):
        os.mkdir(pos_save_dir)
    f_gt_pos = open(os.path.join(save_path, "train_gt_%d.txt" % net_size), 'w')
    for label_info in annotations:
        dict_info = dataset_indicator.label_parser(label_info)
        image_name = dict_info['image_name']
        # Get attribute ground truth
        gt_boxes = dict_info['gt_boxes']
        image = cv2.imread(image_name)
        idx += 1
        if idx % 100 == 0:
            print(idx, "images done")

        height, width, channel = image.shape
        for box in gt_boxes:
            # box (x_left, y_top, x_right, y_bottom)
            x1, y1, x2, y2 = box
            w = x2 - x1 + 1
            h = y2 - y1 + 1

            # ignore small faces
            if max(w, h) < 24 or x1 < 0 or y1 < 0:
                continue
            # Add ground truth samples
            best_side = int(np.sqrt(w * h))
            best_x1 = int(max(x1 + (w - best_side) / 2, 0))
            best_y1 = int(max(y1 + (h - best_side) / 2, 0))
            best_x2 = int(best_x1 + best_side - 1)
            best_y2 = int(best_y1 + best_side - 1)
            if best_x2 < width and best_y2 < height:
                gt_img = image[best_y1:best_y2 + 1, best_x1:best_x2 + 1, :]
                resized_im = cv2.resize(gt_img, (net_size, net_size), interpolation=cv2.INTER_LINEAR)
                offset_x1 = (x1 - best_x1) / float(best_side)
                offset_y1 = (y1 - best_y1) / float(best_side)
                offset_x2 = (x2 - best_x2) / float(best_side)
                offset_y2 = (y2 - best_y2) / float(best_side)
                save_file = os.path.join(pos_save_dir, "%d.jpg" % p_idx)
                f_gt_pos.write("%d/train_gt_positive/%d.jpg" % (net_size, p_idx) + ' 1 %.2f %.2f %.2f %.2f\n'
                               % (offset_x1, offset_y1, offset_x2, offset_y2))
                cv2.imwrite(save_file, resized_im)
                p_idx += 1


def parse_args():
    parser = argparse.ArgumentParser(description='Generate hard train and verify data.',
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('--test_mode', dest='test_mode', help='test net type, can be pnet, rnet or onet',
                        default='pnet', type=str)
    parser.add_argument('--prefix', dest='prefix', help='prefix of model name', nargs="+",
                        default=['../models/pnet/pnet_OHEM_0.7_wo_pooling/pnet',
                                 '../models/rnet/rnet_wider_OHEM_0.7_wop_pnet/rnet',
                                 ''],
                        type=str)
    parser.add_argument('--epoch', dest='epoch', help='epoch number of model to load', nargs="+",
                        default=[13, 16, 16], type=int)
    parser.add_argument('--batch_size', dest='batch_size', help='list of batch size used in prediction', nargs="+",
                        default=[2048, 256, 16], type=int)
    parser.add_argument('--thresh', dest='thresh', help='list of thresh for pnet, rnet, onet', nargs="+",
                        default=[0.4, 0.1, 0.7], type=float)
    parser.add_argument('--min_face', dest='min_face', help='minimum face size for detection',
                        default=48, type=int)
    args = parser.parse_args()
    return args


if __name__ == '__main__':
    args = parse_args()
    print ('Called with argument:')
    print (args)
    train_dataset_indicator = WIDER('train')
    val_dataset_indicator = WIDER('val')

    stage = 0
    train_pickle_name = osp.join(osp.join(data_dir, 'cands_train_%s%d_0.4_0.1.pkl' % (add_dir_name, net_size)))
    val_pickle_name = osp.join(osp.join(data_dir, 'cands_val_%s%d_0.4_0.1.pkl' % (add_dir_name, net_size)))
    if stage == 1:
        # Load model and dataset_indicator
        detector = DetectAPI(args.prefix, args.epoch, args.test_mode, args.batch_size, False, args.thresh, args.min_face)
        # 1. Detect and save pickle
        detect_save_pickle(detector, train_dataset_indicator, train_pickle_name)
        detect_save_pickle(detector, val_dataset_indicator, val_pickle_name)

    elif stage == 2:
        # 2. Crop and save face samples by pickle file
        save_hard_example(train_dataset_indicator, train_pickle_name)
        save_hard_example(val_dataset_indicator, val_pickle_name)


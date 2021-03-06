#!/usr/bin/env python
# encoding: utf-8
"""
Train classify network, such as pnet and rnet and onet without auxiliary task
"""
import os
import sys
from datetime import datetime
import numpy as np
import tensorflow as tf
import os.path as osp
sys.path.append(osp.join('.'))
from prepare_data import stat_tfrecords
from nets import JDAP_Net
import train_core
import re
import hp_config
flags = hp_config.flags
FLAGS = flags.FLAGS

LR_EPOCH = [7, 13]

os.environ.setdefault('CUDA_VISIBLE_DEVICES', FLAGS.gpu_id)


def eval_net(net_factory, val_tfrecords, net_size):
    # Get val data from tfrecords.
    val_cls_data_label = stat_tfrecords.ReadTFRecord(val_tfrecords, net_size, 3)
    val_image_batch, val_cls_label_batch, val_reg_label_batch = \
        tf.train.batch([val_cls_data_label['image'], val_cls_data_label['cls_label'],
                        val_cls_data_label['reg_label']], batch_size=FLAGS.batch_size, num_threads=16,
                       allow_smaller_final_batch=True)
    val_cls_prob, val_bbox_pred, _ = net_factory(val_image_batch, is_training=False, mode='VERIFY')
    # Return eval op
    eval_cls_op, eval_bbox_pred_op = train_core.compute_accuracy(
        val_cls_prob, val_bbox_pred, val_cls_label_batch, val_reg_label_batch)
    return eval_cls_op, eval_bbox_pred_op


def train_net(net_factory, model_prefix, logdir, end_epoch, net_size, tfrecords, val_tfrecords=[], frequent=500):
    # Set logging verbosity
    tf.logging.set_verbosity(tf.logging.INFO)
    with tf.Graph().as_default():
        #########################################
        # Get Detect train data from tfrecords. #
        #########################################
        cls_data_label = stat_tfrecords.ReadTFRecord(tfrecords, net_size, 3)
        # https://stackoverflow.com/questions/43028683/whats-going-on-in-tf-train-shuffle-batch-and-tf-train-batch?answertab=votes#tab-top
        # https://stackoverflow.com/questions/34258043/getting-good-mixing-with-many-input-datafiles-in-tensorflow/34258214#34258214
        # Different batch_size and capacity and min_after_dequeue impact data selected.
        image_batch, cls_label_batch, reg_label_batch = \
            tf.train.shuffle_batch([cls_data_label['image'], cls_data_label['cls_label'], cls_data_label['reg_label']],
                                   batch_size=FLAGS.batch_size, capacity=20000, min_after_dequeue=10000, num_threads=16,
                                   allow_smaller_final_batch=True)
        if len(val_tfrecords):
            eval_cls_op, eval_bbox_pred_op = eval_net(net_factory, val_tfrecords, net_size)
        # Network Forward
        if FLAGS.is_ERC:
            cls_prob_op, bbox_pred_op, ERC1_loss_op, ERC2_loss_op, cls_loss_op, bbox_loss_op, end_points = \
                net_factory(image_batch, cls_label_batch, reg_label_batch)
        else:
            cls_prob_op, bbox_pred_op, cls_loss_op, bbox_loss_op, end_points = \
                net_factory(image_batch, cls_label_batch, reg_label_batch, mode='TRAIN')

        #########################################
        # Configure the optimization procedure. #
        #########################################
        global_step = tf.Variable(0, trainable=False)

        boundaries = [int(epoch * FLAGS.image_sum / FLAGS.batch_size) for epoch in LR_EPOCH]
        lr_values = [FLAGS.lr * (FLAGS.lr_decay_factor ** x) for x in range(0, len(LR_EPOCH) + 1)]
        lr_op = tf.train.piecewise_constant(global_step, boundaries, lr_values)

        optimizer = train_core.configure_optimizer(lr_op)
        if FLAGS.is_ERC:
            total_loss = ERC1_loss_op + ERC2_loss_op + cls_loss_op + bbox_loss_op
        else:
            total_loss = train_core.task_add_weight(cls_loss_op, bbox_loss_op)

        train_op = optimizer.minimize(total_loss, global_step)
        #########################################
        # Save train/verify summary.            #
        #########################################
        tf.summary.scalar('learning_rate', lr_op)
        tf.summary.scalar('cls_loss', cls_loss_op)
        tf.summary.scalar('bbox_reg_loss', bbox_loss_op)
        if FLAGS.is_ERC:
            tf.summary.scalar('loss_ERC1', ERC1_loss_op)
            tf.summary.scalar('loss_ERC2', ERC2_loss_op)
            tf.summary.scalar('loss_last', cls_loss_op)
            tf.summary.scalar('loss_sum', ERC1_loss_op + ERC2_loss_op + cls_loss_op + bbox_loss_op)
        else:
            tf.summary.scalar('loss_sum', cls_loss_op + bbox_loss_op)

        # Save feature map parameters
        if FLAGS.is_feature_visual:
            for feature_name, feature_val in end_points.items():
                print(feature_name)
                tf.summary.histogram(feature_name, feature_val)

        #########################################
        # Check point or retrieve model         #
        #########################################
        model_dir = model_prefix.rsplit('/', 1)[0]
        if not os.path.exists(model_dir):
            os.makedirs(model_dir)
        latest_ckpt = tf.train.latest_checkpoint(model_dir)
        # Adaptive use gpu memory
        tf_config = tf.ConfigProto()
        # tf_config.gpu_options.per_process_gpu_memory_fraction = 0.4
        tf_config.gpu_options.allow_growth = True
        sess = tf.Session(config=tf_config)
        saver = tf.train.Saver()
        coord = tf.train.Coordinator()
        if latest_ckpt is not None:
            saver.restore(sess, latest_ckpt)
            start_epoch = int(next(re.finditer("(\d+)(?!.*\d)", latest_ckpt)).group(0))
        else:
            sess.run(tf.global_variables_initializer())
            start_epoch = 1

        threads = tf.train.start_queue_runners(sess=sess, coord=coord)
        summary_writer = tf.summary.FileWriter(logdir, sess.graph)
        summary_op = tf.summary.merge_all()

        #########################################
        # Main Training/Verify Loop             #
        #########################################
        # allow_smaller_final_batch, avoid discarding data
        n_step_epoch = int(np.ceil(FLAGS.image_sum / FLAGS.batch_size))
        for cur_epoch in range(start_epoch, end_epoch + 1):
            cls_loss_list = []
            bbox_loss_list = []

            for batch_idx in range(n_step_epoch):
                _, cls_pred, bbox_pred, cls_loss, bbox_loss, lr, summary_str, gb_step = \
                    sess.run([train_op, cls_prob_op, bbox_pred_op, cls_loss_op, bbox_loss_op,
                              lr_op, summary_op, global_step])

                cls_loss_list.append(cls_loss)
                bbox_loss_list.append(bbox_loss)

                if not batch_idx % frequent:
                    summary_writer.add_summary(summary_str, gb_step)
                    print("%s: Epoch: %d, cls loss: %4f, bbox loss: %4f learning_rate: %4f"
                          % (datetime.now(), cur_epoch, np.mean(cls_loss_list), np.mean(bbox_loss_list), lr))
                    sys.stdout.flush()
            print("%s: Epoch: %d, cls loss: %4f, bbox loss: %4f " %
                  (datetime.now(), cur_epoch, np.mean(cls_loss_list), np.mean(bbox_loss_list)))
            saver.save(sess, model_prefix, cur_epoch)
            # Computer val set accuracy, using the same as train batch_size
            if len(val_tfrecords):
                n_step_val = int(np.ceil(FLAGS.val_image_sum) / FLAGS.batch_size)
                val_cls_prob = []
                val_bbox_error = []
                for step in range(n_step_val):
                    val_prob, val_bbox = sess.run([eval_cls_op, eval_bbox_pred_op])
                    val_cls_prob.append(val_prob)
                    val_bbox_error.append(val_bbox)
                print("Epoch: %d, cls accuracy: %4f" % (cur_epoch, np.mean(val_cls_prob)))
                print("Bbox_reg_square_mean_deviation: " + str(np.mean(val_bbox_error, axis=0)))
        coord.request_stop()
        coord.join(threads)


def main(_):
    global_param_dict = tf.app.flags.FLAGS.__dict__['__flags']
    for k, v in global_param_dict.items():
        print (k, v)

    if FLAGS.image_size == 12:
        #net_factory = JDAP_Net.JDAP_12Net
        net_factory = JDAP_Net.JDAP_12Net_wo_pooling
        #net_factory = JDAP_Net.JDAP_12Net_wop_relu6
    elif FLAGS.image_size == 18:
        net_factory = JDAP_Net.JDAP_mNet
    elif FLAGS.image_size == 24:
        if FLAGS.is_ERC:
            net_factory = JDAP_Net.JDAP_24Net_ERC
        else:
            #net_factory = JDAP_Net.JDAP_24Net
            #net_factory = JDAP_Net.JDAP_24Net_wop
            net_factory = JDAP_Net.JDAP_mNet_normal
    elif FLAGS.image_size == 48:
        net_factory = JDAP_Net.JDAP_48Net
        #net_factory = JDAP_Net.JDAP_aNet_Cls

    ''' TFRecords input'''
    cls_tfrecords = []
    val_tfrecords = []
    tfrecords_num = FLAGS.tfrecords_num
    tfrecords_root = FLAGS.tfrecords_root
    for i in range(tfrecords_num):
        print(tfrecords_root + "-%.5d-of-0000%d" % (i, tfrecords_num))
        cls_tfrecords.append(tfrecords_root + "-%.5d-of-0000%d" % (i, tfrecords_num))
    print(cls_tfrecords)
    # for i in range(tfrecords_num):
    #     print(tfrecords_root + "_val-%.5d-of-0000%d" % (i, tfrecords_num))
    #     val_tfrecords.append(tfrecords_root + "_val-%.5d-of-0000%d" % (i, tfrecords_num))

    train_net(net_factory=net_factory, model_prefix=FLAGS.model_prefix, logdir=FLAGS.logdir,
              end_epoch=FLAGS.end_epoch, net_size=FLAGS.image_size, tfrecords=cls_tfrecords,
              val_tfrecords=val_tfrecords, frequent=FLAGS.frequent)


if __name__ == '__main__':

    tf.app.run()
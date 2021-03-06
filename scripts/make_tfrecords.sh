#!/usr/bin/env bash

# This script make tfrecords by multi-threads:
#
# Usage:
# ./scripts/make_tfrecords.sh
IMAGE_SIZE=48
ROOT_PATH='/home/dafu/data/jdap_data/'
LABEL_FILE="/home/dafu/data/jdap_data/${IMAGE_SIZE}/train_${IMAGE_SIZE}.txt"
python ./prepare_data/multithread_create_tfrecords.py \
    --image_root_path=${ROOT_PATH} \
    --dataset_file=${LABEL_FILE} \
    --dataset_name='onet_wop_pnet' \
    --output_dir='./tfrecords/onet' \
    --num_shards=4 \
    --num_threads=4 \
    --image_size=${IMAGE_SIZE} \
    --is_shuffle=True
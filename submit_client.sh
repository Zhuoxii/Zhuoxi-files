spark-submit \
    --master yarn \
    --deploy-mode client \
    --num-executors 20 \
    A2.py \
    --output $1

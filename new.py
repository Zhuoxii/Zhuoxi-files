# -*- coding: utf-8 -*-
"""Assignment2.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1YJNRT2dwVl68QCqueI5OElPsBQ0KJ7pY
"""

from pyspark.sql.functions import explode, col
from pyspark.sql.functions import udf
from pyspark.sql.functions import lit
from pyspark.sql.functions import locate
from pyspark.sql.functions import size
from pyspark.sql import functions as f
from pyspark.sql.types import StructType, StructField, LongType, StringType, ArrayType, IntegerType
from pyspark.sql.window import Window
from pyspark.sql.functions import count
import argparse

from pyspark.sql import SparkSession
spark = SparkSession \
    .builder \
    .appName("Comp5349 Assignment2") \
    .config("spark.executor.memory", "4g")\
    .config("spark.driver.memory", "6g") \
    .config("spark.executor.cores", "4") \
    .getOrCreate()



spark.sparkContext.setLogLevel("ERROR")
parser = argparse.ArgumentParser()
parser.add_argument("--output", help="the output path",
                        default='a2_out')
args = parser.parse_args()
output_path = args.output

    # s3://comp5349-2022/test.json
    # s3://comp5349-2022/train_separate_questions.json
    # s3://comp5349-2022/CUADv1.json
data = "s3://comp5349-2022/test.json"
df= spark.read.option('multiline', 'true').json(data)

df_title = df.select(explode("data.title").alias("title")).withColumn("index",f.monotonically_increasing_id())
df_para = df.select(explode("data.paragraphs").alias("data")).withColumn("index",f.monotonically_increasing_id())


df = df_title.join(df_para,df_title.index==df_para.index).drop("index")\
         .select('title','data',explode("data.context").alias("context"))\
         .select('title','data', 'context', explode("data.qas").alias("qas"))\
         .select('title', 'context', 'qas', explode("qas").alias("qas2"))\
         .select('title', 'context', 'qas2', "qas2.question", "qas2.answers","qas2.is_impossible").cache()

df.show()

def segmentToSequence(data):
  ls = []
  ix = []
  n = 0
  for i in range(len(data)):
    if i >= 4096 and i % 2048 == 0:
      res = list(data[n:i])
      res = "".join(res)
      ls.append(res)
      ix.append([n,i])
      n += 2048
  return ls


udf1=udf(segmentToSequence, ArrayType(StringType()))

### impossible negative
  
df_no_answer = df.filter(df.is_impossible == True)  #.select('context','question','answers' ,"is_impossible")
df_impossible_negative = df_no_answer.withColumn('list_context',udf1(df_no_answer.context))\
                       .withColumn("source",f.explode('list_context'))\
                       .withColumn('type', lit('impossible negative'))\
                       .withColumn('answer_start', lit(0))\
                       .withColumn('answer_end', lit(0))\
                       .select('title','context', 'source', 'question', 'answer_start', 'answer_end', 'type')

# df_impossible_negative.show()

df_answer = df.filter(df.is_impossible == False )\
             .withColumn('answers2', explode('answers').alias('answers2'))\
             .select('title','context','question','answers2.text', 'answers2.answer_start',"is_impossible")
df_answer = df_answer.withColumn('list_context',udf1('context'))\
           .withColumn("source",f.explode('list_context')).select('title','context', 'source', 'question','text', 'answer_start', 'is_impossible').cache()
# df_answer.show()

def answer_start(source,text):
  index = source.find(text)
  if index == -1:
    return 0
  else:
    return index

def answer_end(source,text):
  index = source.find(text)
  length = len(text)
  if index == -1:
    return 0
  else:
    return index+length

def Type(AnswerStart,AnswerEnd,is_impossible):
  if is_impossible: # impossible negative
    return 0
  elif not is_impossible and AnswerStart==0 and AnswerEnd==0: # possible negative
    return 1
  else: # positive
    return 2


udf1 = udf(answer_start,LongType())
udf2 = udf(answer_end,LongType())
udf3 = udf(Type,LongType())

df_possible = df_answer.withColumn('answer_start',udf1(df_answer.source,df_answer.text))\
    .withColumn('answer_end',udf2(df_answer.source,df_answer.text))\
    .withColumn('type',udf3('answer_end','answer_start',df_answer.is_impossible))\
    .select("title","source","question","answer_start","answer_end","type")

df_possible.show()

df_possible_negative = df_possible.filter(df_possible.type == 1).cache()
df_positive = df_possible.filter(df_possible.type == 2).cache()


"""# Balance negative and positive samples"""

#对于每个contract每个question有多少sample
##对于positive而言

df_1 = df_positive.groupBy('question').count().withColumnRenamed('count','question_count')
df_3 = df_positive.groupBy('question').agg(f.countDistinct('title')).withColumnRenamed('count(title)','other_contract_count')
df_4 = df_1.join(df_3, 'question','inner')
df_1 = df_4.withColumn('extract_length',f.round(f.col('question_count')/f.col('other_contract_count'),0).astype('int'))

# df_1.show()

## 把postive的question和impossible negative的question join
df_2 =  df_1.join(df_impossible_negative, 'question', 'inner').orderBy('title','question','source')\
          .select('title', 'question','source', 'extract_length','type')

# df_2.show()

window1 = Window.partitionBy("title").orderBy('title','question')
df_3 = df_2.groupBy('title','question','extract_length').agg(f.collect_set('source').alias('source_list')).orderBy('title','question')
df_3 = df_3.withColumn('seq_len', f.size('source_list'))

df_4 = df_3.withColumn('extract_length2',f.when(df_3.extract_length <=  df_3.seq_len ,df_3.extract_length).otherwise(df_3.seq_len))\
          .drop('extract_length')

import random

def extract(source_list, n):
  ls = list(source_list)
  # ls2 = random.shuffle(ls)
  res = ls[:n]
  return res


udf2 = udf(extract, ArrayType(StringType()))
impossible_negative = df_4.withColumn('extract_source', udf2(f.col("source_list"), f.col('extract_length2')))

impossible_negative =  impossible_negative.withColumn('source', explode(f.col('extract_source')))\
                                  .withColumn('answer_start', lit(0))\
                                  .withColumn('answer_end', lit(0))\
                                  .select('source', 'question', 'answer_start', 'answer_end')
impossible_negative.show()


# impossible_negative.show()

"""## Balance possible negative and postive"""

df1 = df_positive.groupBy('title', 'question').count().withColumnRenamed('count','extract_length')

df2 = df_possible_negative.join(df1, ['title','question'], 'inner')
df3 = df2.groupBy('title','question','extract_length').agg(f.collect_set('source').alias('source_list')).orderBy('title','question')\
          .withColumn('seq_len', f.size('source_list'))


df4 = df3.withColumn('extract_length2',f.when(df3.extract_length <=  df3.seq_len ,df3.extract_length).otherwise(df3.seq_len))\
          .drop('extract_length')

possible_negative = df4.withColumn('extract_source', udf2(f.col("source_list"), f.col('extract_length2')))

# possible_negative = df4.withColumn('extract_source', f.slice("source_list",start= lit(1), length=f.col('extract_length2')))
possible_negative =  possible_negative.withColumn('source', explode(f.col('extract_source')))\
                                  .withColumn('answer_start', lit(0))\
                                  .withColumn('answer_end', lit(0))\
                                  .select('source', 'question', 'answer_start', 'answer_end')
possible_negative.show()


# print("successfully!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!")

"""# 结果合并"""

positive = df_positive.select('source', 'question', 'answer_start','answer_end')
df_all = positive.union(impossible_negative).union(possible_negative).cache()
# df_all.show()

import json
result = df_all.toJSON().collect()
output = json.dumps(result, indent = 2)
with open('result.json','w') as f:
  json.dump(output, f)


spark.stop()


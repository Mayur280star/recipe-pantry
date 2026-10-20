# Recipe Selection & Substitution Platform (lite demo)

Type the ingredients you have and get recipes ranked by how much of each recipe you can already make, with the missing
ingredients and rule-based substitutes. 231,583 real recipes from the Food.com corpus
([Kaggle](https://www.kaggle.com/datasets/shuyangli94/food-com-recipes-and-user-interactions), Majumder et al., EMNLP 2019).

This app runs the project's **lite profile**: the same API and ranking code as the full big data stack (HDFS, Spark, Hive
Metastore, Trino, SolrCloud), served here by an in-memory index and DuckDB over the published Parquet snapshot. Both
profiles return identical results.

Riddhesh V. Budha, Mayur D. Chaudhari, Yashodeep Y. Chaudhary and Prof. Bhavesh Panchal,
Department of Computer Engineering, Rajiv Gandhi Institute of Technology, Mumbai.

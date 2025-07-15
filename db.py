from pymongo import MongoClient

client = MongoClient("mongodb+srv://iconnectionbb:9l7i8Zsb5vZqDQJ3@cluster0.bxvnl5a.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0")
db = client['cable_recharge']

recharges = db['recharges']
users = db['users']  # NEW LINE

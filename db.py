import os
from pymongo import MongoClient
from dotenv import load_dotenv
import certifi

# Load environment variables from .env
load_dotenv()

# Recommended: Store this in .env
mongo_uri = "mongodb+srv://iconnectionbb:connectionbb@cluster0.bxvnl5a.mongodb.net/?retryWrites=true&w=majority&appName=Cluster0"

# Setup TLS with certifi
ca = certifi.where()
client = MongoClient(mongo_uri, tls=True, tlsCAFile=ca)

# Connect to database and collections
db = client['cable_recharge']
recharges = db['recharges']
users = db['users']

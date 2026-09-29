import time
from pymongo import MongoClient

URI = "mongodb://localhost:27017/?directConnection=true"
INDEX = "vec_idx"
QUERY = [0.9, 0.1, 0.0, 0.0]


def coll():
    return MongoClient(URI, serverSelectionTimeoutMS=5000)["m0_spike"]["chunks"]


def wait_queryable(c, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        idx = list(c.list_search_indexes(INDEX))
        if idx and idx[0].get("queryable"):
            return idx[0]
        time.sleep(2)
    raise TimeoutError(list(c.list_search_indexes(INDEX)))


def search(c):
    return list(c.aggregate([
        {"$vectorSearch": {"index": INDEX, "path": "embedding", "queryVector": QUERY, "numCandidates": 10, "limit": 3}},
        {"$project": {"_id": 1, "text": 1, "score": {"$meta": "vectorSearchScore"}}},
    ]))

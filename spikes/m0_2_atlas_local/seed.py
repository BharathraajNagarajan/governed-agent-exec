from pymongo.operations import SearchIndexModel
from common import INDEX, coll, search, wait_queryable

c = coll()
c.drop()
c.insert_many([
    {"_id": 1, "text": "restart consumer", "embedding": [1.0, 0.0, 0.0, 0.0]},
    {"_id": 2, "text": "scale consumer", "embedding": [0.8, 0.2, 0.0, 0.0]},
    {"_id": 3, "text": "pause pipeline", "embedding": [0.0, 1.0, 0.0, 0.0]},
    {"_id": 4, "text": "reset offset", "embedding": [0.0, 0.0, 1.0, 0.0]},
    {"_id": 5, "text": "check lag", "embedding": [0.0, 0.0, 0.0, 1.0]},
])
c.create_search_index(SearchIndexModel(
    name=INDEX,
    type="vectorSearch",
    definition={"fields": [{"type": "vector", "path": "embedding", "numDimensions": 4, "similarity": "cosine"}]},
))
print("index", wait_queryable(c))
print("count", c.count_documents({}))
for r in search(c):
    print(r)

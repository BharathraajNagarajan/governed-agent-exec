from common import coll, search, wait_queryable

c = coll()
print("count", c.count_documents({}))
print("index", wait_queryable(c))
for r in search(c):
    print(r)

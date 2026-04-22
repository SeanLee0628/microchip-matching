import os
import boto3
import json
from boto3.dynamodb.conditions import Key

AWS_REGION = os.environ.get("AWS_REGION", "ap-northeast-2")
TABLE_PREFIX = os.environ.get("DYNAMO_TABLE_PREFIX", "mm_")


def get_dynamodb():
    return boto3.resource("dynamodb", region_name=AWS_REGION)


def get_table(name):
    return get_dynamodb().Table(f"{TABLE_PREFIX}{name}")


def create_tables():
    client = get_dynamodb().meta.client
    existing = client.list_tables()["TableNames"]

    tables = {
        "ublox": {"pk": "upload_version", "sk": "order_name"},
        "sales": {"pk": "batch_id", "sk": "item_id"},
    }

    for name, schema in tables.items():
        table_name = f"{TABLE_PREFIX}{name}"
        if table_name in existing:
            continue
        key_schema = [{"AttributeName": schema["pk"], "KeyType": "HASH"}]
        attr_defs = [{"AttributeName": schema["pk"], "AttributeType": "S"}]
        if "sk" in schema:
            key_schema.append({"AttributeName": schema["sk"], "KeyType": "RANGE"})
            attr_defs.append({"AttributeName": schema["sk"], "AttributeType": "S"})
        client.create_table(
            TableName=table_name, KeySchema=key_schema,
            AttributeDefinitions=attr_defs, BillingMode="PAY_PER_REQUEST",
        )
        print(f"Created: {table_name}")


class DTable:
    def __init__(self, name):
        self.table = get_table(name)

    def put(self, item):
        clean = {k: v for k, v in item.items() if v is not None and v != "" and str(v) != "nan"}
        self.table.put_item(Item=clean)

    def get(self, pk, sk=None):
        key = {"pk": pk}
        if sk:
            key["sk"] = sk
        resp = self.table.get_item(Key=key)
        return resp.get("Item")

    def query(self, pk_name, pk_value):
        items = []
        kwargs = {"KeyConditionExpression": Key(pk_name).eq(pk_value)}
        while True:
            resp = self.table.query(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return items

    def scan_all(self):
        items = []
        kwargs = {}
        while True:
            resp = self.table.scan(**kwargs)
            items.extend(resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        return items

    def delete_all(self):
        items = self.scan_all()
        key_schema = self.table.key_schema
        with self.table.batch_writer() as batch:
            for item in items:
                key = {ks["AttributeName"]: item[ks["AttributeName"]] for ks in key_schema}
                batch.delete_item(Key=key)

    def delete_by_pk(self, pk_name, pk_value):
        items = self.query(pk_name, pk_value)
        key_schema = self.table.key_schema
        with self.table.batch_writer() as batch:
            for item in items:
                key = {ks["AttributeName"]: item[ks["AttributeName"]] for ks in key_schema}
                batch.delete_item(Key=key)

import os
from time import sleep

import pytest
from fastapi.testclient import TestClient
from rubric.server.commons.models import TaskStatus
from rubric.server.server import app
from rubric.server.snapshots.model import DatasetSnapshot
from rubric.server.text_classification.model import (
    TaskType,
    TextClassificationBulkData,
    TextClassificationRecord,
)

client = TestClient(app)


def create_some_data_for_text_classification(name: str):
    records = [
        TextClassificationRecord(**data)
        for data in [
            {
                "inputs": {"data": "my data"},
                "multi_label": True,
                "metadata": {"field_one": "value one", "field_two": "value 2"},
                "status": TaskStatus.validated,
                "annotation": {
                    "agent": "test",
                    "labels": [
                        {"class": "Test"},
                        {"class": "Mocking"},
                    ],
                },
            },
            {
                "inputs": {"data": "my data"},
                "multi_label": True,
                "metadata": {"field_one": "another value one", "field_two": "value 2"},
                "status": TaskStatus.validated,
                "prediction": {
                    "agent": "test",
                    "labels": [
                        {"class": "NoClass"},
                    ],
                },
            },
        ]
    ]
    client.post(
        f"/api/datasets/{name}/{TaskType.text_classification}/:bulk",
        json=TextClassificationBulkData(
            name=name,
            tags={"env": "test", "class": "text classification"},
            metadata={"config": {"the": "config"}},
            records=records,
        ).dict(by_alias=True),
    )

    sleep(1)


def uri_2_path(uri: str):
    from urllib.parse import urlparse

    p = urlparse(uri)
    return os.path.abspath(os.path.join(p.netloc, p.path))


def test_dataset_snapshots_flow():
    name = "test_create_dataset_snapshot"
    api_ds_prefix = f"/api/datasets/{name}"
    assert client.delete(api_ds_prefix).status_code == 200
    # Clear eventually already created snapshots
    response = client.get(f"{api_ds_prefix}/snapshots")
    for snapshot in map(DatasetSnapshot.parse_obj, response.json()):
        assert (
            200 == client.delete(f"{api_ds_prefix}/snapshots/{snapshot.id}").status_code
        )

    create_some_data_for_text_classification(name)
    response = client.post(
        f"{api_ds_prefix}/snapshots?task={TaskType.text_classification}"
    )
    assert response.status_code == 200

    with pytest.raises(ValueError):
        client.post(f"{api_ds_prefix}/snapshots?task={TaskType.token_classification}")

    snapshot = DatasetSnapshot(**response.json())
    assert os.path.exists(uri_2_path(snapshot.uri))

    response = client.get(f"{api_ds_prefix}/snapshots")
    assert response.status_code == 200
    snapshots = list(map(DatasetSnapshot.parse_obj, response.json()))
    assert len(snapshots) == 1
    assert snapshots[0] == snapshot

    response = client.get(f"{api_ds_prefix}/snapshots/{snapshot.id}")
    assert response.status_code == 200
    assert snapshot == DatasetSnapshot(**response.json())

    client.delete(f"{api_ds_prefix}/snapshots/{snapshot.id}")
    response = client.get(f"{api_ds_prefix}/snapshots/{snapshot.id}")
    assert response.status_code == 404
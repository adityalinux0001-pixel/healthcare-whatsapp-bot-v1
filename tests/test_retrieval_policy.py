from app.knowledge import store


class FakeEmbedder:
    def encode(self, query, normalize_embeddings=True):
        return [0.1, 0.2, 0.3]


class FakeCollection:
    def query(self, **kwargs):
        return {
            "ids": [["survey-1", "auth-1"]],
            "documents": [["survey text", "official text"]],
            "metadatas": [[
                {"source_role": "supporting_only", "authority": 0.1, "source_name": "Survey"},
                {"source_role": "authoritative", "authority": 1.0, "source_name": "ICMR-NIN"},
            ]],
            "distances": [[0.1, 0.2]],
        }


def test_retrieve_defaults_to_authoritative_lane(monkeypatch):
    monkeypatch.setattr(store, "kb_ready", lambda: True)
    monkeypatch.setattr(store, "get_embedder", lambda: FakeEmbedder())
    monkeypatch.setattr(store, "get_collection", lambda: FakeCollection())

    rows = store.retrieve("What is a healthy diet?", n_results=8)

    assert len(rows) == 1
    assert rows[0]["metadata"]["source_role"] == "authoritative"
    assert rows[0]["metadata"]["source_name"] == "ICMR-NIN"

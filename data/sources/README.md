# Hair & Scalp knowledge sources

The runtime knowledge base is intentionally limited to paraphrased, topic-based summaries in `external/hair_loss_guidelines.json`, grounded in the public guidance linked in that file from the American Academy of Dermatology (AAD) and NHS.

User onboarding answers and conversation content are stored in PostgreSQL and must never be indexed into ChromaDB. The bundled source file is a compact retrieval aid, not a substitute for reading the linked guidance or clinical review. Verify source URLs and terms periodically before commercial use.

## Rebuild

```bash
python scripts/build_knowledge_base.py --replace
```

`--replace` clears the existing Chroma collection and rebuilds it with hair/scalp material only. Back up any persistent Chroma directory before running this command.

# Runtime knowledge base

ChromaDB data is generated during image build or by `python scripts/build_knowledge_base.py --replace`; it should not be committed as a static source artifact. The builder indexes only the hair/scalp authoritative summaries in `data/sources/external/hair_loss_guidelines.json`. User-specific profile data is never indexed.

The `manifest.json` records the source hash, metadata, and indexing count from the latest build.

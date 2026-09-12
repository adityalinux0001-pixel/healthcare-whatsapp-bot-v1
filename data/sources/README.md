# Knowledge-base sources

## Authoritative diet
- `../raw/DGI_2024.pdf` — ICMR-NIN Dietary Guidelines for Indians 2024. Use as the primary diet-guidance source after confirming permission for electronic/product use.

## Authoritative exercise
- `external/exercise_guidelines.json` — compact retrieval summary sourced from WHO and CDC official guidance; URLs are stored with the facts.

## Candidate authoritative sources (not bundled as KB evidence yet)
- Indian Food Composition Tables (IFCT) 2017 — useful for future food-level nutrient calculations, but do not reproduce/bundle until usage terms are cleared.
- ICMR-NIN RDA/EAR material — useful for nutrient requirement calculations after permission/clinical review.

## Supporting datasets
- `personalized_diet_recommendations.csv` and `diet_recommendations.csv` can be explicitly included with `--include-supporting` for non-clinical menu/personalization examples.
- `food_behavior_survey.csv` and `def_survey_responses.csv` are never indexed by the builder. They are survey/behavior data, not health evidence.

## Build
```bash
python scripts/build_knowledge_base.py --replace
```
The manifest records file hashes, source role, authority, provenance and indexing counts.

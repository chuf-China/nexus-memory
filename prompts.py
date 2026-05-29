"""nexus/prompts.py — Zep-level extraction prompts

Prompt engineering for entity extraction, fact extraction, entity resolution,
and temporal extraction. Designed to match Graphiti's extraction quality.

Based on analysis of:
- Zep/Graphiti open-source prompts (Apache 2.0)
- Zep paper: arXiv:2501.13956
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Pydantic Output Models ──────────────────────────────────

try:
    from pydantic import BaseModel, Field, field_validator

    class ExtractedEntity(BaseModel):
        name: str = Field(..., min_length=1, max_length=200)
        type: int = Field(..., ge=1, le=8)
        summary: str = Field(default="", max_length=500)

    class ExtractedFact(BaseModel):
        source_entity: str = Field(..., min_length=1)
        target_entity: str = Field(..., min_length=1)
        relation_type: str = Field(..., pattern=r'^[A-Z][A-Z_]+$')
        fact: str = Field(..., min_length=1)
        valid_at: Optional[str] = None
        invalid_at: Optional[str] = None
        confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    class ExtractionResult(BaseModel):
        entities: List[ExtractedEntity] = Field(default_factory=list)
        facts: List[ExtractedFact] = Field(default_factory=list)

    class EntityResolutionResult(BaseModel):
        match: bool
        matched_id: Optional[str] = None
        confidence: float = Field(ge=0.0, le=1.0)
        reasoning: str = ""

    class ConflictDetectionResult(BaseModel):
        relationship: str = Field(..., pattern=r'^(CONTRADICTS|UPDATES|REFINES|DUPLICATE|COMPATIBLE)$')
        action: str = Field(..., pattern=r'^(supersede|merge|add|skip)$')
        confidence: float = Field(ge=0.0, le=1.0)
        reasoning: str = ""

    _PYDANTIC_AVAILABLE = True

except ImportError:
    _PYDANTIC_AVAILABLE = False
    logger.debug("Pydantic not installed, output validation disabled")


def parse_llm_json(text: str) -> Optional[Any]:
    """Parse JSON from LLM output, handling markdown fences and partial output."""
    # Strip markdown code fences
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r'^```\w*\n?', '', text)
        text = re.sub(r'\n?```$', '', text)
    text = text.strip()

    # Try direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to find JSON array or object
    for start, end in [('[', ']'), ('{', '}')]:
        i = text.find(start)
        j = text.rfind(end)
        if i >= 0 and j > i:
            try:
                return json.loads(text[i:j+1])
            except json.JSONDecodeError:
                pass
    return None


def validate_extraction_result(raw: str) -> Optional["ExtractionResult"]:
    """Parse and validate LLM extraction output against Pydantic models."""
    if not _PYDANTIC_AVAILABLE:
        return None
    data = parse_llm_json(raw)
    if data is None:
        return None
    try:
        if isinstance(data, dict):
            return ExtractionResult(**data)
        elif isinstance(data, list):
            return ExtractionResult(entities=[], facts=data)
    except Exception as e:
        logger.debug("Pydantic validation failed: %s", e)
    return None


def validate_entity_resolution(raw: str) -> Optional["EntityResolutionResult"]:
    """Parse and validate entity resolution output."""
    if not _PYDANTIC_AVAILABLE:
        return None
    data = parse_llm_json(raw)
    if data is None or not isinstance(data, dict):
        return None
    try:
        return EntityResolutionResult(**data)
    except Exception as e:
        logger.debug("Entity resolution validation failed: %s", e)
    return None


def validate_conflict_detection(raw: str) -> Optional["ConflictDetectionResult"]:
    """Parse and validate conflict detection output."""
    if not _PYDANTIC_AVAILABLE:
        return None
    data = parse_llm_json(raw)
    if data is None or not isinstance(data, dict):
        return None
    try:
        return ConflictDetectionResult(**data)
    except Exception as e:
        logger.debug("Conflict detection validation failed: %s", e)
    return None

# ── Entity Extraction ────────────────────────────────────────

ENTITY_EXTRACTION_SYSTEM = """You are an entity extraction specialist for conversational messages.
NEVER extract abstract concepts, feelings, or generic words."""

ENTITY_EXTRACTION_PROMPT = """NEVER extract any of the following:
- Pronouns (you, me, I, he, she, they, we, us, it, them, him, her, this, that, those)
- Abstract concepts or feelings (joy, balance, growth, resilience, happiness, passion, motivation)
- Generic common nouns or bare object words (day, life, people, work, stuff, things, food, time, way, tickets, supplies, clothes, keys, gear)
- Generic media/content nouns unless uniquely identified (photo, pic, picture, image, video, post, story)
- Generic event/activity nouns unless uniquely identified (event, game, meeting, class, workshop, competition)
- Broad institutional nouns unless explicitly named (government, school, company, team, office)
- Ambiguous bare nouns whose meaning depends on sentence context
- Sentence fragments or clauses ("what you really care about", "results of that effort")
- Adjectives or descriptive phrases ("amazing", "something different", "new hair color")
- Duplicate references to the same real-world entity (extract each entity at most once per message)
- Bare relational or kinship terms (dad, mom, mother, father, sister, brother, husband, wife, spouse, son, daughter, uncle, aunt, cousin, grandma, grandpa, friend, boss, teacher, neighbor, roommate)
  → Instead, qualify with possessor: "Nisha's dad" not "dad", "Jordan's dog" not "dog"
- Bare generic objects that cannot be meaningfully qualified (e.g., NEVER extract "supplies" from "I picked up some supplies")

Your task is to extract **entity nodes** that are **explicitly** mentioned in the CURRENT MESSAGE.
Pronoun references such as he/she/they or this/that/those should be disambiguated to the names of the reference entities.

ENTITY TYPES (classify each entity):
1. Person — named individuals (张三, Alice, Dr. Smith)
2. Organization — companies, teams, institutions (Google, OpenAI, 开发团队)
3. Technology — tools, frameworks, languages, services (Python, React, PostgreSQL, AWS)
4. Project — specific projects, products, initiatives (Nexus, Hermes Agent, Project Alpha)
5. Location — physical or virtual locations (北京, us-east-1, /home/user)
6. Concept — specific domain concepts that are NOT generic (Kubernetes pod, SQL injection, REST API)
7. Document — specific documents, papers, configs (README.md, CONFIG.md, RFC 2822)
8. Event — specific named events (re:Invent 2026, sprint planning, incident #1234)

DO NOT extract Concepts that are too broad (e.g., "programming", "AI", "cloud").

OUTPUT FORMAT (JSON array):
[
  {{"name": "entity name", "type": 1, "summary": "one-line description if available"}},
  ...
]
Type is the numeric ID from the ENTITY TYPES list above.

CURRENT MESSAGE:
{message}

{previous_context}

ENTITIES (JSON):"""


# ── Fact/Edge Extraction ─────────────────────────────────────

FACT_EXTRACTION_SYSTEM = """You are an expert fact extractor that extracts fact triples from text.
1. Extracted fact triples should include relevant date information when available.
2. Use each message's timestamp to resolve temporal references within that message.
REFERENCE_TIME is a fallback for when no per-message timestamp is available."""

FACT_EXTRACTION_PROMPT = """Given the following ENTITIES and MESSAGE, extract all factual relationships.

ENTITIES:
{entities}

MESSAGE:
{message}

{reference_time}

# TASK
Extract all factual relationships between the given ENTITIES based on the MESSAGE.
Only extract facts that:
- involve two DISTINCT ENTITIES from the ENTITIES list,
- are clearly stated or unambiguously implied in the MESSAGE,
- can be represented as edges in a knowledge graph.
- Facts should include entity names rather than pronouns whenever possible.

# EXTRACTION RULES
1. **Entity Name Validation**: source_entity and target_entity must use only the name values from the ENTITIES list provided above.
   - CRITICAL: Using names not in the list will cause the edge to be rejected
2. Each fact must involve two **distinct** entities — source_entity and target_entity NEVER refer to the same entity.
3. Prefer facts that involve two distinct entities from the ENTITIES list.
4. When a sentence describes a specific, concrete detail about a single entity (a brand name, a specific item, a physical description, a quantity, a location, a named activity), do NOT drop it. Instead, look for a second entity in the ENTITIES list to pair it with. If none exists, pair it with the entity it most directly modifies.
5. Extract facts even when phrased negatively (e.g., "X is not Y") — the negation is part of the fact.
6. **Temporal Information**: Extract dates/times mentioned in relation to facts.
   - "as of January 2025" → valid_at = "2025-01-01T00:00:00Z"
   - "until last week" → invalid_at = [date of last week]
   - "currently" → valid_at = reference_time
   - No time mentioned → valid_at = null, invalid_at = null

# RELATION TYPE RULES
- Use SCREAMING_SNAKE_CASE (e.g., WORKS_AT, USES, DEPENDS_ON, LOCATED_IN, VERSION_IS)
- Be specific: "USES" not "HAS", "VERSION_IS" not "IS"
- Common relation types:
  USES, DEPENDS_ON, PART_OF, LOCATED_IN, WORKS_AT, CREATED_BY,
  VERSION_IS, CONFIGURED_AS, REPLACED_BY, CAUSES, SOLVES,
  COMMUNICATES_WITH, AUTHENTICATES_WITH, DEPLOYED_ON

OUTPUT FORMAT (JSON array):
[
  {{
    "source_entity": "entity name from ENTITIES list",
    "target_entity": "entity name from ENTITIES list",
    "relation_type": "SCREAMING_SNAKE_CASE",
    "fact": "natural language description of this relationship",
    "valid_at": "ISO 8601 or null",
    "invalid_at": "ISO 8601 or null",
    "confidence": 0.0-1.0
  }},
  ...
]

If no facts found, return [].

Facts (JSON):"""


# ── Entity Resolution ────────────────────────────────────────

ENTITY_RESOLUTION_SYSTEM = """You are an entity resolution specialist.
Your task is to determine if a new entity refers to the same real-world entity as any entity in an existing list."""

ENTITY_RESOLUTION_PROMPT = """NEW ENTITY: {new_entity_name} (type: {new_entity_type})

EXISTING ENTITIES:
{existing_entities}

# TASK
Determine if the NEW ENTITY refers to the same real-world entity as any of the EXISTING ENTITIES.

# MATCHING RULES
1. **Name variants**: "张三" = "三哥" = "Zhang San" = "Z. San" = "三儿"
2. **Abbreviations**: "React" = "React.js" = "ReactJS", "Postgres" = "PostgreSQL"
3. **Same person with title**: "CEO" = "张总" (only if context confirms same person)
4. **Case differences**: "python" = "Python", "macOS" = "MacOS" = "macos"
5. **DO NOT match**: Different people with same surname ("张三" ≠ "张四")
6. **DO NOT match**: Different instances of same type ("Python 3.11" ≠ "Python 3.12" if both are distinct)
7. **When uncertain**: Do NOT match. Precision is more important than recall.

OUTPUT FORMAT (JSON):
{{
  "match": true/false,
  "matched_id": "id of matched existing entity" (null if no match),
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation of why match or no match"
}}"""


# ── Temporal Extraction ──────────────────────────────────────

TEMPORAL_EXTRACTION_SYSTEM = """You are a temporal information extractor.
Extract time-related information from text and normalize to ISO 8601 format."""

TEMPORAL_EXTRACTION_PROMPT = """Extract temporal information from the following fact.

FACT: {fact}
REFERENCE_TIME: {reference_time} (ISO 8601 UTC)

# TASK
Determine the temporal bounds of this fact:
- valid_at: When did this fact become true? (when was it established/stated?)
- invalid_at: When did this fact stop being true? (if mentioned or implied)

# RULES
1. "currently", "now", "as of now" → valid_at = reference_time
2. "since January" → valid_at = YYYY-01-01T00:00:00Z
3. "until last week" → invalid_at = [calculated date]
4. "used to", "previously", "in the past" → invalid_at = reference_time (fact is no longer true)
5. "will be", "planned for" → valid_at = [future date if specified]
6. No temporal info → valid_at = null, invalid_at = null
7. Relative dates ("last week", "next month") should be resolved relative to reference_time.

OUTPUT FORMAT (JSON):
{{
  "valid_at": "ISO 8601 or null",
  "invalid_at": "ISO 8601 or null",
  "temporal_reasoning": "brief explanation"
}}"""


# ── Fact Conflict Detection ──────────────────────────────────

CONFLICT_DETECTION_SYSTEM = """You are a fact conflict detector.
Determine if a new fact contradicts, updates, or is compatible with an existing fact."""

CONFLICT_DETECTION_PROMPT = """EXISTING FACT:
Subject: {existing_subject}
Predicate: {existing_predicate}
Object: {existing_object}
Confidence: {existing_confidence}
Created: {existing_created}

NEW FACT:
Subject: {new_subject}
Predicate: {new_predicate}
Object: {new_object}
Confidence: {new_confidence}

# TASK
Determine the relationship between the existing fact and the new fact.

# POSSIBLE RELATIONSHIPS
1. CONTRADICTS — The new fact directly contradicts the existing one (different values for same S-P)
   Example: "Python version 3.11" vs "Python version 3.12"
2. UPDATES — The new fact supersedes the existing one (more recent, more specific, or higher confidence)
   Example: "price is $500" → "price is $450" (price changed)
3. REFINES — The new fact adds detail to the existing one without contradicting it
   Example: "uses Python" → "uses Python 3.12" (more specific)
4. DUPLICATE — The new fact is essentially the same as the existing one
   Example: "PostgreSQL version 16" vs "PostgreSQL 16" (same fact, different phrasing)
5. COMPATIBLE — The new fact is about the same subject but a different predicate
   Example: "PostgreSQL version 16" vs "PostgreSQL port 5432"

OUTPUT FORMAT (JSON):
{{
  "relationship": "CONTRADICTS|UPDATES|REFINES|DUPLICATE|COMPATIBLE",
  "action": "supersede|merge|add|skip",
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation"
}}"""


# ── Summary Generation ───────────────────────────────────────

ENTITY_SUMMARY_SYSTEM = """You are an entity summarizer.
Generate a concise, informative summary of an entity based on its associated facts."""

ENTITY_SUMMARY_PROMPT = """ENTITY: {entity_name} (type: {entity_type})

ASSOCIATED FACTS:
{facts}

EXISTING SUMMARY: {existing_summary}

# TASK
Generate or update a concise summary (1-2 sentences) of this entity based on the facts.
The summary should capture the most important and recent information.
If an existing summary is provided, update it with new information rather than replacing it entirely.

OUTPUT FORMAT (JSON):
{{
  "summary": "concise 1-2 sentence summary"
}}"""


# ── Helper Functions ─────────────────────────────────────────

def build_entity_extraction_prompt(
    message: str,
    previous_messages: Optional[List[str]] = None,
) -> tuple[str, str]:
    """Build entity extraction system+user prompt pair."""
    prev_ctx = ""
    if previous_messages:
        prev_ctx = "PREVIOUS MESSAGES (for context only, extract entities only from CURRENT MESSAGE):\n"
        for i, msg in enumerate(previous_messages[-3:]):  # last 3 messages
            prev_ctx += f"  [{i}] {msg[:200]}\n"

    return (
        ENTITY_EXTRACTION_SYSTEM,
        ENTITY_EXTRACTION_PROMPT.format(
            message=message,
            previous_context=prev_ctx,
        )
    )


def build_fact_extraction_prompt(
    entities: List[Dict[str, Any]],
    message: str,
    reference_time: Optional[str] = None,
) -> tuple[str, str]:
    """Build fact extraction system+user prompt pair."""
    entity_list = "\n".join(
        f"  - {e['name']} (type: {e.get('type', 'Unknown')})"
        for e in entities
    )

    time_ref = ""
    if reference_time:
        time_ref = f"REFERENCE_TIME: {reference_time} (ISO 8601 UTC)"

    return (
        FACT_EXTRACTION_SYSTEM,
        FACT_EXTRACTION_PROMPT.format(
            entities=entity_list,
            message=message,
            reference_time=time_ref,
        )
    )


def build_resolution_prompt(
    new_entity_name: str,
    new_entity_type: str,
    existing_entities: List[Dict[str, Any]],
) -> tuple[str, str]:
    """Build entity resolution prompt pair."""
    existing_list = "\n".join(
        f"  - id={e['id']}, name={e['name']}, type={e.get('type', 'Unknown')}"
        for e in existing_entities
    )

    return (
        ENTITY_RESOLUTION_SYSTEM,
        ENTITY_RESOLUTION_PROMPT.format(
            new_entity_name=new_entity_name,
            new_entity_type=new_entity_type,
            existing_entities=existing_list,
        )
    )


def build_conflict_prompt(
    existing_fact: Dict[str, Any],
    new_fact: Dict[str, Any],
) -> tuple[str, str]:
    """Build conflict detection prompt pair."""
    return (
        CONFLICT_DETECTION_SYSTEM,
        CONFLICT_DETECTION_PROMPT.format(
            existing_subject=existing_fact.get('subject', ''),
            existing_predicate=existing_fact.get('predicate', ''),
            existing_object=existing_fact.get('object', ''),
            existing_confidence=existing_fact.get('confidence', 1.0),
            existing_created=existing_fact.get('created_at', ''),
            new_subject=new_fact.get('subject', ''),
            new_predicate=new_fact.get('predicate', ''),
            new_object=new_fact.get('object', ''),
            new_confidence=new_fact.get('confidence', 1.0),
        )
    )


def build_summary_prompt(
    entity_name: str,
    entity_type: str,
    facts: List[Dict[str, Any]],
    existing_summary: str = "",
) -> tuple[str, str]:
    """Build entity summary prompt pair."""
    fact_list = "\n".join(
        f"  - {f['predicate']}: {f['object']} (confidence: {f.get('confidence', 1.0)})"
        for f in facts
    )

    return (
        ENTITY_SUMMARY_SYSTEM,
        ENTITY_SUMMARY_PROMPT.format(
            entity_name=entity_name,
            entity_type=entity_type,
            facts=fact_list,
            existing_summary=existing_summary or "None",
        )
    )

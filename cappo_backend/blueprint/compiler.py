"""Deterministic blueprint compiler.

Turns a plain-language intent into a reviewable plan against the capability
catalog CAPPO actually serves. It never grants anything: a compiled blueprint
only says which steps a mount could cover, which the catalog blocks, and which
no capability covers at all. Authority is still issued per action at execution.

Deterministic by construction: the same intent against the same catalog always
yields the same plan id and content hash (no clock, no randomness, no model).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Iterable, Literal

from cappo_backend.capability_mount.models import CapabilityPackage

COMPILER_VERSION = "blueprint-compiler/1"

StepKind = Literal["read", "write", "blocked", "uncovered"]
Verdict = Literal["ready_for_mount", "blocked", "incomplete", "empty"]

# Verb families. A clause's verb is matched to an action's verb through these, so
# "show the counter" binds to counter.read and "bump the counter" to counter.increment.
_VERB_FAMILIES: dict[str, frozenset[str]] = {
    "read": frozenset({
        "read", "get", "show", "list", "view", "check", "inspect", "fetch", "look",
        "see", "display", "report", "count", "query", "find", "search", "lookup",
    }),
    "increment": frozenset({"increment", "increase", "bump", "raise", "add", "plus", "inc"}),
    "reset": frozenset({"reset", "zero", "clear", "wipe", "restart"}),
    "delete": frozenset({"delete", "remove", "erase", "purge", "drop", "destroy", "wipe"}),
    "create": frozenset({"create", "make", "new", "open", "start", "register"}),
    "update": frozenset({"update", "change", "edit", "modify", "set", "write", "patch"}),
    "send": frozenset({"send", "email", "notify", "message", "post", "publish", "share"}),
}
# Verbs that change or destroy state; an uncovered clause using one is a real gap.
_CONSEQUENTIAL = {"increment", "reset", "delete", "create", "update", "send"}
_CLAUSE_SPLIT = re.compile(r"\s*(?:,|;|\band then\b|\bthen\b|\band\b|\.)\s*", re.IGNORECASE)
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset({"the", "a", "an", "my", "our", "its", "it", "to", "of", "for", "on", "in", "by", "one", "please"})


@dataclass(frozen=True)
class _Action:
    package: CapabilityPackage
    action: str
    resource: str
    verb: str
    kind: StepKind


def _stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    return word


def _words(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP]


def _families(word: str) -> set[str]:
    return {family for family, verbs in _VERB_FAMILIES.items() if word in {_stem(v) for v in verbs}}


def _catalog_actions(packages: Iterable[CapabilityPackage]) -> list[_Action]:
    actions: list[_Action] = []
    for package in sorted(packages, key=lambda p: p.id):
        for kind, names in (("read", package.reads), ("write", package.writes), ("blocked", package.blocked)):
            for name in names:
                resource, _, verb = name.rpartition(".")
                actions.append(_Action(package, name, _stem(resource or name), _stem(verb or name), kind))  # type: ignore[arg-type]
    return actions


def _clause_matches(words: list[str], action: _Action) -> bool:
    if action.resource not in words:
        return False
    verb_families = _families(action.verb) or {action.verb}
    return any(w == action.verb or _families(w) & verb_families for w in words)


def catalog_digest(packages: Iterable[CapabilityPackage]) -> str:
    canonical = [p.model_dump(mode="json") for p in sorted(packages, key=lambda p: p.id)]
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def compile_blueprint(intent: str, packages: Iterable[CapabilityPackage]) -> dict:
    """Compile ``intent`` against ``packages`` into a deterministic, reviewable plan."""
    packages = list(packages)
    intent = " ".join(intent.split())
    actions = _catalog_actions(packages)
    clauses = [c for c in _CLAUSE_SPLIT.split(intent) if _words(c)]
    resources = {a.resource for a in actions}
    clause_words = [_words(c) for c in clauses]
    # "read and increment the counter": a clause with a verb but no catalog object
    # borrows the object from the nearest clause that names one (next first).
    for i, words in enumerate(clause_words):
        if resources & set(words):
            continue
        for j in [*range(i + 1, len(clause_words)), *range(i - 1, -1, -1)]:
            borrowed = resources & set(clause_words[j])
            if borrowed:
                clause_words[i] = words + sorted(borrowed)
                break

    steps: list[dict] = []
    for index, clause in enumerate(clauses, start=1):
        words = clause_words[index - 1]
        matched = [a for a in actions if _clause_matches(words, a)]
        if matched:
            # A clause can only be one step; prefer the most restrictive reading so a
            # destructive request is never silently downgraded to a permitted one.
            order = {"blocked": 0, "write": 1, "read": 2}
            best = sorted(matched, key=lambda a: (order[a.kind], a.package.id, a.action))[0]
            steps.append({
                "id": f"step_{index}",
                "clause": clause,
                "kind": best.kind,
                "action": best.action,
                "package_ref": best.package.id,
                "reason": (
                    f"{best.package.id} blocks {best.action}; CAPPO will deny it even under a mount"
                    if best.kind == "blocked"
                    else f"covered by {best.package.id} ({best.kind}); a permit is still issued per action"
                ),
            })
            continue
        consequential = any(_families(w) & _CONSEQUENTIAL for w in words)
        steps.append({
            "id": f"step_{index}",
            "clause": clause,
            "kind": "uncovered",
            "action": None,
            "package_ref": None,
            "reason": (
                "no capability in the catalog covers this consequential step"
                if consequential
                else "no capability in the catalog covers this step"
            ),
        })

    kinds = {s["kind"] for s in steps}
    verdict: Verdict
    if not steps:
        verdict = "empty"
    elif "uncovered" in kinds:
        verdict = "incomplete"
    elif "blocked" in kinds:
        verdict = "blocked"
    else:
        verdict = "ready_for_mount"

    # Suggested mount scope per package: exactly the actions the plan uses, and the
    # package's blocked list carried as explicit blocks.
    mounts: dict[str, dict] = {}
    for step in steps:
        ref = step["package_ref"]
        if not ref:
            continue
        package = next(p for p in packages if p.id == ref)
        scope = mounts.setdefault(ref, {"package_ref": ref, "reads": [], "writes": [], "blocked": list(package.blocked)})
        bucket = {"read": "reads", "write": "writes"}.get(step["kind"])
        if bucket and step["action"] not in scope[bucket]:
            scope[bucket].append(step["action"])

    nodes = [{"id": "intent", "type": "intent", "description": intent[:120], "policy_tag": "intent"}]
    edges: list[dict] = []
    previous = "intent"
    mounted: set[str] = set()
    for step in steps:
        ref = step["package_ref"]
        if ref and ref not in mounted:
            mount_id = f"mount:{ref}"
            nodes.append({"id": mount_id, "type": "mount", "description": f"Mount {ref}", "policy_tag": "authority"})
            edges.append({"from": previous, "to": mount_id})
            previous = mount_id
            mounted.add(ref)
        nodes.append({
            "id": step["id"],
            "type": step["kind"],
            "description": step["action"] or step["clause"][:80],
            "policy_tag": step["kind"],
        })
        edges.append({"from": previous, "to": step["id"]})
        previous = step["id"]

    digest = catalog_digest(packages)
    content = {
        "compiler": COMPILER_VERSION,
        "intent": intent,
        "catalog_digest": digest,
        "steps": steps,
        "suggested_mounts": list(mounts.values()),
        "verdict": verdict,
        "graph": {"nodes": nodes, "edges": edges},
    }
    content_hash = hashlib.sha256(json.dumps(content, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "id": f"bp_{content_hash[:16]}",
        "status": "compiled",
        "name": f"Blueprint: {intent[:60]}" if intent else "Blueprint",
        **content,
        "proof_hash": f"sha256:{content_hash}",
        "grants_authority": False,
    }

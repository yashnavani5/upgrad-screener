"""Merge duplicate founders of the same company ("Minh Thu Do" / "Do Minh Thu", "Lam Do" / "Do Ngoc Lam").

Runs at the end of `screener resolve` and as `screener founders dedupe`. The kept record is the one with the most
claims (then a LinkedIn URL, then the longest name); claims, company links and discoveries move to it.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from screener.models import Claim, CompanyFounder, Discovery, Founder
from screener.resolve.normalize import same_person

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Merge:
    company_id: Any
    kept: str
    merged: str


def duplicate_groups(names: dict[Any, str]) -> list[list[Any]]:
    """Group founder ids whose names are the same person (transitively). Pure: {founder_id: name} -> groups."""
    ids = list(names)
    parent = {i: i for i in ids}

    def find(i: Any) -> Any:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a_idx, a in enumerate(ids):
        for b in ids[a_idx + 1:]:
            if same_person(names[a], names[b]):
                parent[find(b)] = find(a)
    groups: dict[Any, list[Any]] = defaultdict(list)
    for i in ids:
        groups[find(i)].append(i)
    return [g for g in groups.values() if len(g) > 1]


def merge_duplicate_founders(session: Session) -> list[Merge]:
    links = session.execute(select(CompanyFounder.company_id, CompanyFounder.founder_id)).all()
    by_company: dict[Any, list[Any]] = defaultdict(list)
    for cid, fid in links:
        by_company[cid].append(fid)
    founders = {f.id: f for f in session.scalars(select(Founder))}
    claim_counts = dict(session.execute(select(Claim.entity_id, func.count()).where(Claim.entity_type == "founder")
                                        .group_by(Claim.entity_id)).all())
    merges: list[Merge] = []
    gone: set[Any] = set()
    for cid, fids in by_company.items():
        names = {f: founders[f].full_name for f in fids if f in founders and f not in gone}
        for group in duplicate_groups(names):
            group.sort(key=lambda f: (-claim_counts.get(f, 0), founders[f].linkedin_url is None, -len(founders[f].full_name)))
            keep, *dups = group
            k = founders[keep]
            for d in dups:
                dup = founders[d]
                session.execute(update(Claim).where(Claim.entity_type == "founder", Claim.entity_id == d).values(entity_id=keep))
                session.execute(update(Discovery).where(Discovery.founder_id == d).values(founder_id=keep))
                for link in session.scalars(select(CompanyFounder).where(CompanyFounder.founder_id == d)).all():
                    exists = session.scalar(select(CompanyFounder).where(CompanyFounder.founder_id == keep,
                                                                         CompanyFounder.company_id == link.company_id))
                    if exists is None:
                        link.founder_id = keep
                    else:
                        session.delete(link)
                session.flush()
                k.linkedin_url = k.linkedin_url or dup.linkedin_url
                k.other_urls = sorted(set(k.other_urls or []) | set(dup.other_urls or []))
                k.identity_verified = k.identity_verified or dup.identity_verified
                k.current_title = k.current_title or dup.current_title
                k.lookups = {**(dup.lookups or {}), **(k.lookups or {})}
                session.execute(delete(Founder).where(Founder.id == d))
                gone.add(d)
                merges.append(Merge(cid, k.full_name, dup.full_name))
                log.warning("merged founder %r into %r", dup.full_name, k.full_name)
    session.flush()
    return merges

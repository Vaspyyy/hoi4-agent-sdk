"""Plain-language summaries of what pending edits change."""

from __future__ import annotations

import re

from ..characters import (
    role_key,
)
from ..focus import load_focus_tree
from ._core import CoreLayer
from ._shared import (
    _LOAD_FOCUS_TREE_RE,
    _join_script_parts,
)


class PreviewLayer(CoreLayer):
    """Plain-language summaries of what pending edits change."""

    def _semantic_preview_lines(self) -> list[str]:
        lines: list[str] = []
        if "assets" in self._dirty:
            for path in sorted(self._pending_asset_writes):
                lines.append(
                    f"asset {path.relative_to(self.mod_root).as_posix()}: stage"
                )
        if "country_names" in self._dirty:
            for tag in sorted(self._country_name_pool_updates):
                lines.append(f"country name pool {tag}: create/update")
        if "countries" in self._dirty:
            for tag in sorted(self._dirty_countries):
                country = self._countries.get(tag)
                if country:
                    lines.append(f"country {tag}: create/update {country.name or tag}")
        if "characters" in self._dirty:
            for character_id in sorted(self._dirty_characters):
                character = self._characters.get(character_id)
                if character is None:
                    lines.append(f"character {character_id}: delete")
                else:
                    role_names = ",".join(
                        role_key(role) for role in character.roles
                    )
                    lines.append(
                        f"character {character_id}: roles={role_names or '<instance-only>'}"
                    )
        if "oobs" in self._dirty:
            for name in sorted(self._dirty_oobs):
                oob = self._oobs.get(name)
                if oob is None:
                    lines.append(f"OOB {name}: delete")
                else:
                    lines.append(
                        f"OOB {name}: {len(oob.templates)} template(s), "
                        f"{len(oob.divisions)} division(s)"
                    )
        if "states" in self._dirty:
            for sid in sorted(self._dirty_states):
                state = self._states.get(sid)
                if state:
                    lines.append(
                        f"state {sid}: owner={state.owner or '<none>'} cores={','.join(state.cores) or '<none>'}"
                    )
            for sid in sorted(self._deleted_state_ids):
                lines.append(f"state {sid}: delete")
        if "map" in self._dirty:
            for relative in sorted(self._map_file_updates):
                lines.append(f"map file {relative}: update")
        if "ideas" in self._dirty:
            for iid in sorted(self._dirty_ideas):
                idea = self._ideas.get(iid)
                if idea:
                    lines.append(
                        f"idea {iid}: category={idea.category or '<none>'} modifiers={','.join(sorted(idea.modifier)) or '<none>'}"
                    )
        if "ideologies" in self._dirty:
            for ideology_id in sorted(self._dirty_ideologies):
                ideology = self._ideologies.get(ideology_id)
                if ideology is not None:
                    lines.append(
                        f"ideology {ideology_id}: {len(ideology.types)} subtype(s)"
                    )
            for path in sorted(self._dirty_ideology_files):
                lines.append(f"ideology file {self._display_path(path)}: updated")
        if "dynamic_modifiers" in self._dirty:
            for modifier_id in sorted(self._dirty_dynamic_modifiers):
                if modifier_id in self._dynamic_modifiers:
                    lines.append(f"dynamic modifier {modifier_id}: create/update")
                else:
                    lines.append(f"dynamic modifier {modifier_id}: deleted")
        if "bookmarks" in self._dirty:
            for path in sorted(self._dirty_bookmark_files):
                count = sum(bookmark.path == path for bookmark in self._bookmarks)
                lines.append(
                    f"bookmark file {self._display_path(path)}: {count} bookmark(s)"
                )
        if "bookmark_dates" in self._dirty and self._bookmark_date_defines is not None:
            _, start_date, end_date = self._bookmark_date_defines
            lines.append(f"bookmark date range: {start_date} through {end_date}")
        if "events" in self._dirty:
            for eid in sorted(self._dirty_events):
                event = self._events.get(eid)
                if event:
                    lines.append(f"event {eid}: {len(event.options)} option(s)")
                    event_script = _join_script_parts(
                        [event.immediate] + [option.effect for option in event.options]
                    )
                    lines.extend(self._effect_semantic_lines(event_script, f"event {eid}"))
        if "on_actions" in self._dirty:
            for action_id in sorted(self._dirty_on_actions):
                if action_id in self._on_actions:
                    lines.append(f"on_action {action_id}: updated")
            for path in sorted(self._dirty_on_action_files):
                lines.append(f"on_action file {self._display_path(path)}: rewritten")
        if "decisions" in self._dirty:
            for cid in sorted(self._dirty_decision_categories):
                category = self._decision_categories.get(cid)
                if category:
                    lines.append(f"decision category {cid}: {len(category.decisions)} decision(s)")
                    for decision in category.decisions:
                        lines.extend(
                            self._effect_semantic_lines(
                                _join_script_parts(
                                    [
                                        decision.complete_effect,
                                        decision.remove_effect,
                                        decision.timeout_effect,
                                        decision.cancel_effect,
                                    ]
                                ),
                                f"decision {decision.id}",
                            )
                        )
        if "focus" in self._dirty:
            for tree_id in sorted(self._dirty_focus_trees):
                tree = self._focus_trees.get(tree_id)
                if tree is None:
                    continue
                old_tree = load_focus_tree(tree.path) if tree.path and tree.path.exists() else None
                old_focuses = {focus.id: focus for focus in old_tree.focuses} if old_tree else {}
                new_focuses = {focus.id: focus for focus in tree.focuses}
                if (
                    old_tree
                    and old_tree.continuous_focus_position != tree.continuous_focus_position
                ):
                    lines.append(
                        f"{tree_id} continuous_focus_position: "
                        f"{old_tree.continuous_focus_position or '<none>'} -> {tree.continuous_focus_position or '<none>'}"
                    )
                for fid in sorted(new_focuses):
                    focus = new_focuses[fid]
                    old = old_focuses.get(fid)
                    if old is None:
                        lines.append(f"{tree_id} focus {fid}: added at x={focus.x} y={focus.y}")
                        lines.extend(
                            self._effect_semantic_lines(
                                focus.completion_reward, f"{tree_id} focus {fid}"
                            )
                        )
                        continue
                    changes: list[str] = []
                    if (old.x, old.y) != (focus.x, focus.y):
                        changes.append(f"position x={old.x} y={old.y} -> x={focus.x} y={focus.y}")
                    if old.completion_reward.strip() != focus.completion_reward.strip():
                        changes.append("completion_reward changed")
                    if old.icon != focus.icon:
                        changes.append(f"icon {old.icon} -> {focus.icon}")
                    if changes:
                        lines.append(f"{tree_id} focus {fid}: {'; '.join(changes)}")
                        if old.completion_reward.strip() != focus.completion_reward.strip():
                            lines.extend(
                                self._effect_semantic_lines(
                                    focus.completion_reward, f"{tree_id} focus {fid}"
                                )
                            )
                for fid in sorted(set(old_focuses) - set(new_focuses)):
                    lines.append(f"{tree_id} focus {fid}: removed")
        if "localization" in self._dirty:
            lines.append(f"localization: {len(self._dirty_loc_keys)} key(s)")
            for language, dirty_keys in sorted(self._foreign_dirty_loc_keys.items()):
                if dirty_keys:
                    lines.append(f"localization ({language}): {len(dirty_keys)} key(s)")
        return lines

    def _effect_semantic_lines(self, script: str, owner: str) -> list[str]:
        if not script:
            return []
        lines: list[str] = []
        for tree_id in _LOAD_FOCUS_TREE_RE.findall(script):
            lines.append(f"{owner}: load focus tree {tree_id}")
        for tag, state_id in re.findall(
            r"\b([A-Z][A-Z0-9]{2})\s*=\s*\{[^{}]*\btransfer_state\s*=\s*([0-9]+)\b",
            script,
            flags=re.DOTALL,
        ):
            lines.append(f"{owner}: transfer state {state_id} to {tag}")
        for state_id, tag in re.findall(
            r"\b([0-9]+)\s*=\s*\{[^{}]*\badd_core_of\s*=\s*([A-Z][A-Z0-9]{2})\b",
            script,
            flags=re.DOTALL,
        ):
            lines.append(f"{owner}: add {tag} core on state {state_id}")
        for state_id, tag in re.findall(
            r"\b([0-9]+)\s*=\s*\{[^{}]*\bremove_core_of\s*=\s*([A-Z][A-Z0-9]{2})\b",
            script,
            flags=re.DOTALL,
        ):
            lines.append(f"{owner}: remove {tag} core from state {state_id}")
        for overlord, puppet in re.findall(
            r"\b([A-Z][A-Z0-9]{2})\s*=\s*\{[^{}]*\bend_puppet\s*=\s*([A-Z][A-Z0-9]{2})\b",
            script,
            flags=re.DOTALL,
        ):
            lines.append(f"{owner}: end puppet {puppet} under {overlord}")
        for puppet in re.findall(r"(?m)^\s*end_puppet\s*=\s*([A-Z][A-Z0-9]{2})\b", script):
            lines.append(f"{owner}: end puppet {puppet}")
        for leader, target in re.findall(
            r"\b([A-Z][A-Z0-9]{2})\s*=\s*\{[^{}]*\badd_to_faction\s*=\s*([A-Z][A-Z0-9]{2})\b",
            script,
            flags=re.DOTALL,
        ):
            lines.append(f"{owner}: {target} joins {leader} faction")
        for actor, target in re.findall(
            r"\b([A-Z][A-Z0-9]{2})\s*=\s*\{[^{}]*\bdeclare_war_on\s*=\s*\{[^{}]*\btarget\s*=\s*([A-Z][A-Z0-9]{2})\b",
            script,
            flags=re.DOTALL,
        ):
            lines.append(f"{owner}: {actor} declares war on {target}")
        for target in re.findall(
            r"(?m)^\s*declare_war_on\s*=\s*\{[^{}]*\btarget\s*=\s*([A-Z][A-Z0-9]{2})\b",
            script,
            flags=re.DOTALL,
        ):
            lines.append(f"{owner}: declare war on {target}")
        seen: set[str] = set()
        unique: list[str] = []
        for line in lines:
            if line not in seen:
                unique.append(line)
                seen.add(line)
        return unique

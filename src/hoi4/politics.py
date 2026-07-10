"""Single source of truth for HOI4 party groups and leader ideologies."""

from __future__ import annotations


LEADER_IDEOLOGIES_BY_PARTY: dict[str, tuple[str, ...]] = {
    "democratic": ("liberalism", "conservatism", "socialism"),
    "communism": ("marxism", "leninism", "stalinism", "anti_revisionism", "anarchist_communism"),
    "fascism": ("nazism", "fascism_ideology", "falangism", "rexism"),
    "neutrality": ("despotism", "oligarchism", "moderate", "centrism"),
}
RULING_PARTIES: tuple[str, ...] = tuple(LEADER_IDEOLOGIES_BY_PARTY)
IDEOLOGY_PARTY_MAP: dict[str, str] = {
    ideology: party
    for party, ideologies in LEADER_IDEOLOGIES_BY_PARTY.items()
    for ideology in ideologies
}

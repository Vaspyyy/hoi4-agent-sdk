#!/usr/bin/env python3
"""Fix: ITA owners, leader ideologies, focus position. Bypasses state serializer to preserve buildings."""
import os, sys
sys.path.insert(0, os.path.dirname(__file__))

from pathlib import Path
from hoi4 import Mod

MOD = Path("/home/ransom/.local/share/Paradox Interactive/Hearts of Iron IV/mod/sdk")
STATES = MOD / "history" / "states"

# ═══════════════════════════════════════════════════════════════
# Fix 1: Rewrite state files directly — ITA owner + new cores
# ═══════════════════════════════════════════════════════════════

states_to_fix = {
    115: ("SCL", """state = {
	id = 115
	name = "STATE_115"

	history = {
		owner = ITA
		victory_points = {
			10074 20 
		}
		victory_points = {
			4159 5 
		}
		victory_points = {
			4014 5 
		}
		victory_points = {
			13256 1 
		}
		buildings = {
			infrastructure = 3
			industrial_complex = 1
			air_base = 10
			dockyard = 1
			10074 = {
				naval_base = 2

			}
			7059 = {
				naval_base = 5

			}
			4159 = {
				naval_base = 8

			}
			4014 = {
				naval_base = 3

			}

		}
		add_core_of = ITA
		add_core_of = TTS
		add_core_of = SCL

	}

	provinces = {
		1009 1156 3857 4014 4159 7059 7147 10074 12047 13256 
	}
	manpower = 3836571
	buildings_max_level_factor = 1.000
	state_category = city
	local_supplies = 0.000
}
"""),
    114: ("SRD", """state = {
	id = 114
	name = "STATE_114"
	manpower = 991921
	
	resources = {
		coal = 1 # initial distribution
	}

	state_category = town
	
	history = {
		owner = ITA
		victory_points = {
			11773 3 
		}
		victory_points = {
			9772 2 
		}
		buildings = {
			infrastructure = 3
			arms_factory = 0
			industrial_complex = 1
			air_base = 5
			11773 = {
				naval_base = 3
			}
			6891 = {
				naval_base = 5
			}
		}
		add_core_of = ITA
		add_core_of = SPM
		add_core_of = SRD
	}
	provinces = {
		902 6891 9772 9863 11755 11773 11824 11874 	
	}

	local_supplies = 0.0 
}
"""),
    160: ("VNT", """state = {
	id = 160
	name = "STATE_160"
	resources = {
		steel=20 # was: 32.000
		aluminium=11 # was: 18.000
	}
	
	state_category = large_city

	history = {
		owner = ITA
		victory_points = { #Venezia
			11584 10 
		}
		victory_points = { #Padova
			3604 3 
		}
		victory_points = { #Verona
			603 1
		}

		buildings = {
			infrastructure = 3 #was: 6
			arms_factory = 2
			industrial_complex = 4
			dockyard = 1
			air_base = 3
			11584 = {
				naval_base = 6
			}
		}
		add_core_of = ITA
		add_core_of = LBV
		add_core_of = VNT
		1939.1.1 = {
			buildings = {
				dockyard = 2
				industrial_complex = 5
			}
		}

	}

	provinces = {
		603 628 3604 3657 6656 9582 9613 11584 
	}
	manpower = 3963151
	buildings_max_level_factor = 1.000

	local_supplies = 5.0 
}
"""),
    163: ("ZAR", """state = {
	id = 163
	name = "STATE_163"
	manpower = 18000
	
	state_category = enclave
	
	history = {
		owner = ITA
		victory_points = { #Zara/Zadar
			3943 5 
		}
		buildings = {
			infrastructure = 3 #was: 5
			3943 = {
				naval_base = 1
			}
		}
		add_core_of = YUG
		add_core_of = CRO
		add_core_of = LBV
		add_core_of = ZAR
		start_resistance = CRO #Yugoslavia shouldn't resist occupation
		set_compliance = 70
	}
	provinces = {	
		3943 
	}

	local_supplies = 0.0 
}
"""),
}

for state_id, (tag, content) in states_to_fix.items():
    f = STATES / f"{state_id}-{tag}.txt"
    # Try the original vanilla filename pattern
    name_map = {115: "Sicily", 114: "Sardinia", 160: "Veneto", 163: "Dalmatia"}
    orig = STATES / f"{state_id}-{name_map[state_id]}.txt"
    target = orig if orig.exists() else f
    if not target.exists():
        target = next(STATES.glob(f"{state_id}*.txt"), STATES / f"{state_id}.txt")
    target.write_text(content, encoding="utf-8")
    print(f"  Fixed state {state_id} ({tag}) -> {target.name}")

# ═══════════════════════════════════════════════════════════════
# Fix 2 + 3: Use SDK for leaders and focus tree
# ═══════════════════════════════════════════════════════════════

mod = Mod.from_config()

# Fix leader ideologies to match neutrality ruling party
for tag, name, ideology in [
    ("SCL", "Andrea Finocchiaro Aprile", "conservatism"),
    ("SRD", "Emilio Lussu", "conservatism"),
    ("VNT", "Silvio Trentin", "conservatism"),
    ("ZAR", "Antonio Bajamonti", "conservatism"),
]:
    mod.update_country(tag, leader_name=name, leader_ideology=ideology)

# Fix continuous focus position
tree = mod.get_focus_tree("scl_focus")
tree.continuous_focus_position = "x = -3\ny = 0"
mod._dirty.add("focus")
mod._dirty_focus_trees.add("scl_focus")

# Save (only countries, localization, focus tree — NOT states)
errors = mod.validate()
error_count = sum(1 for e in errors if e.severity == "error")
print(f"\n  {error_count} errors, {len(errors) - error_count} warnings")
mod.save()
print("  Saved countries + focus tree.")

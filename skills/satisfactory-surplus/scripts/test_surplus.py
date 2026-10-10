"""Self-check for the session arithmetic: uv run python skills/satisfactory-surplus/scripts/test_surplus.py prints ok."""
from surplus_report import building_drain, is_world_file, session_row, world_of

a = {"play_s": 0, "stock": {"Concrete": 5000}, "built": {"Concrete": 40000}}
b = {"play_s": 7200, "stock": {"Concrete": 1000}, "built": {"Concrete": 43000}}
r = session_row(a, b, "Concrete")
assert r == {"play_h": 2.0, "stock_change": -4000, "built": 3000, "factory_net": -1000}, r
assert building_drain(r) == 3000  # 1000 of the fall was hand-feeding, not building
assert building_drain({"stock_change": 500, "built": 3000}) == 0  # production kept up
assert building_drain({"stock_change": -800, "built": 0}) == 0  # nothing built: hand-fed
assert building_drain({"stock_change": -800, "built": -200}) == 0  # dismantled more than built
assert world_of("Base_autosave_2_continue") == world_of("Base_manual") == world_of("Base") == "Base"
assert is_world_file("Base.sav", "Base") and is_world_file("Base_autosave_1.sav", "Base")
assert is_world_file("20261009-1200_Base_autosave_1.sav", "Base")  # a bot's stamped copy
assert not is_world_file("BaseTwo_autosave_1.sav", "Base")
print("ok")

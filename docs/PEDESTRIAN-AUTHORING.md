
## HIPS slot and toe crossover (0.12)

- Every v5 entry gets slot 14 = HIPS at bake time: Tanner's HIPS mesh retargeted from page set 2 to the entry's legs page set (3rd texture page set). Paint the hips on your legs page at Tanner's hips rect (see textures/crops/HIPS_set2_t12.png in the Tanner source model); don't put other crops over that rect. HIPS stays a reference group in model.obj; editing its mesh is not baked yet.
- Toes are crossed like stock Tanner: FOOT_LEFT is drawn on the right toe bone (RTOE) and FOOT_RIGHT on LTOE. Author feet against the bindpose export (it uses the same bindings).
- Ship-with-MP characters live in JERICHO/MODS/mp/peds/<slug>/ (character folders as produced by import/add_character). BAKE_D1_CARS passes that folder to jmpped_player.py --characters; page sets are re-allocated on import, so character.json index/sets need not be unique across folders.
- tannerd3 is regenerated with: python tools/ped/jmpped_make_tannerd3.py --level <HAVANA.LEV> --out JERICHO/MODS/mp/peds (Tanner has no NECK slot in JMPPED; the head sits on the Jericho HEAD bone).

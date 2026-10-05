/* d1cars_module.c - the JERICHO module wrapper for the d1cars unit.
 *
 * d1cars exists as a unit (d1cars.c / d1cars.h) with exactly one entry point,
 * d1cars_register(ctx); this file turns it into a JERICHO MODULE of its own so
 * it can be enabled alongside any other module.
 *
 * The generated registry (Game/C/JERICHO/gen/jer_registry.c) calls
 * jer_module_d1cars_entry to link and the loader calls it again on a reload, so
 * this must stay re-runnable: d1cars_register is documented safe to call more
 * than once.
 *
 * It must remain a COMPILED-IN module (no `runtime = "dll"` in mod.toml). Like
 * carhacks, it will read and write the game's own car-data globals
 * (CarAvailability, carNumLookup, carTpages/...) - data symbols MSVC cannot
 * import into a DLL without dllimport.
 */

#include "jericho.h"

#include "d1cars.h"

JER_MODULE_ENTRY(jer_module_d1cars_entry)(JERICHO_CONTEXT* ctx)
{
	ctx->jer_register_module(ctx,
		"d1cars",			/* id (must match the folder + mod.toml) */
		"Driver 1 Cars",		/* name */
		"0.1.0",			/* version */
		"Jaret Ludvik",			/* author */
		"Driver 1 (1999) traffic car shapes as a fifth, synthetic car-data "
		"source alongside the four Driver 2 cities. SCAFFOLD ONLY - no "
		"behaviour yet; see docs/DRIVER1.md.",
		"",				/* dependencies */
		JERICHO_SDK_VERSION);		/* SDK this module was built against */

	/* the unit: config keys, engine hooks and the pool loader land with the
	 * offline bake pipeline. Safe on a reload. */
	d1cars_register(ctx);
}

/* bake_module.c - the JERICHO module wrapper for the bake unit.
 *
 * bake exists as a unit (bake.c / bake.h) with exactly one entry point,
 * bake_register(ctx); this file turns it into a JERICHO MODULE of its own so
 * it can be enabled alongside any other module.
 *
 * The generated registry (Game/C/JERICHO/gen/jer_registry.c) calls
 * jer_module_bake_entry to link and the loader calls it again on a reload, so
 * this must stay re-runnable: bake_register is documented safe to call more
 * than once.
 *
 * bake is a runtime addon: it builds against the standalone SDK and the
 * engine's car-data globals are reached through JER_HOST_API (the cardata
 * subtable). The offline BAKE tools and baked D1 data stay outside the DLL.
 */

#include "jericho.h"

#include "bake.h"

JER_MODULE_ENTRY(jer_module_bake_entry)(JERICHO_CONTEXT* ctx)
{
	ctx->jer_register_module(ctx,
		"bake",				/* id (must match the folder + mod.toml) */
		"BAKE Toolkit",		/* name */
		"0.3.0",			/* version */
		"Jaret Ludvik",			/* author */
		"Driver 1 (1999) traffic car shapes as a fifth, synthetic car-data "
		"source alongside the four Driver 2 cities. SCAFFOLD ONLY - no "
		"behaviour yet; see docs/DRIVER1.md.",
		"carhacks",			/* dependencies */
		JERICHO_SDK_VERSION);		/* SDK this module was built against */

	/* the unit: config keys, engine hooks and the pool loader land with the
	 * offline bake pipeline. Safe on a reload. */
	bake_register(ctx);
}

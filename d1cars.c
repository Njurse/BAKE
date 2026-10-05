/* d1cars.c - the unit: Driver 1 (1999) traffic car shapes as a fifth car-data
 * source for the engine.
 *
 * This file is the UNIT. It owns d1cars_register(), the single entry point the
 * module wrapper (d1cars_module.c) calls. Keeping the unit separate from the
 * wrapper means the same code can be compiled into another module unchanged -
 * the split carhacks uses.
 *
 * SCAFFOLD (Phase 1). d1cars_register() registers nothing yet: no config keys,
 * no engine hooks, no pool loader. It only records that the unit came up, so a
 * build that includes this module is visibly alive and the entry is provably
 * re-runnable. The real work is described in docs/DRIVER1.md.
 */

#include "jericho.h"

#include "d1cars.h"

/* Logging convention (mirrors carhacks):
 *   - printInfo(...)    every DIAGNOSTIC line - what the module did and why it
 *                       refused. This is the engine's informational channel and
 *                       is what the harnesses read from a run's captured stdout.
 *                       Prefix "[d1cars]".
 *   - jer_error(...)    a line the PLAYER must see. It logs verbatim AND raises
 *                       the on-screen notice - the one call for a refusal.
 *   - ctx->jer_log(...) ONLY inside a module entry/registration function, where
 *                       `ctx` is the handle in hand and calling it is the SDK
 *                       idiom. Lands in the same log as printInfo.
 */

void d1cars_register(JERICHO_CONTEXT* ctx)
{
	/* SCAFFOLD. Nothing to register yet. Safe to call more than once. */
	ctx->jer_log(ctx, "[d1cars] unit registered (scaffold - no hooks yet)\n");
}

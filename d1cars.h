#ifndef D1CARS_H
#define D1CARS_H

/* d1cars - Driver 1 (1999) traffic car shapes, as a fifth car-data source.
 *
 * The goal is to lift the civilian traffic car SHAPES out of the original
 * Driver 1 PSX level files and make them available as a car-data source that
 * sits ALONGSIDE the four Driver 2 cities (Chicago/Havana/Rio/Vegas) rather
 * than replacing one. See docs/DRIVER1.md for the format work and the roadmap.
 *
 * SCAFFOLD (Phase 1). d1cars_register() is the unit's single entry point, called
 * from the module wrapper (d1cars.c). For now it only registers the module's
 * metadata. The config keys, the engine hooks and the pool loader arrive with
 * the offline bake pipeline, so nothing here reads a file or touches engine
 * state yet.
 *
 * MODULE BOUNDARY. Kept HOST-AGNOSTIC and single-entry, exactly like carhacks:
 *   - it will use only engine globals already exported to mods (via exports.def)
 *     and the public JERICHO SDK - no other module's internals;
 *   - it exposes exactly one entry, d1cars_register(ctx), which the module
 *     wrapper calls;
 *   - its symbols are d1cars_-prefixed and its config section is "d1cars".
 *
 * d1cars_register is safe to call more than once (a module reload re-runs the
 * entry).
 */

#include "jericho.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Register the unit: its config keys, its engine hooks and (later) its own
 * car-data source. Called from the module entry, or from another module's entry
 * once it is a module itself. */
void d1cars_register(JERICHO_CONTEXT* ctx);

#ifdef __cplusplus
}
#endif

#endif /* D1CARS_H */

#ifndef BAKE_H
#define BAKE_H

/* bake - the BAKE Toolkit: Driver 1 (1999) traffic car shapes as a fifth
 * car-data source.
 *
 * The goal is to lift the civilian traffic car SHAPES out of the original
 * Driver 1 PSX level files and make them available as a car-data source that
 * sits ALONGSIDE the four Driver 2 cities (Chicago/Havana/Rio/Vegas) rather
 * than replacing one. See docs/DRIVER1.md for the format work and the roadmap.
 *
 * SCAFFOLD (Phase 1). bake_register() is the unit's single entry point, called
 * from the module wrapper (bake_module.c). For now it only registers the
 * module's metadata. The config keys, the engine hooks and the pool loader
 * arrive with the offline bake pipeline, so nothing here reads a file or
 * touches engine state yet.
 *
 * MODULE BOUNDARY. Kept HOST-AGNOSTIC and single-entry, exactly like carhacks:
 *   - it uses only the public JERICHO SDK and JER_HOST_API (the cardata
 *     subtable), never another module's internals;
 *   - it exposes exactly one entry, bake_register(ctx), which the module
 *     wrapper calls;
 *   - its symbols are bake_-prefixed and its config section is "bake".
 *
 * bake_register is safe to call more than once (a module reload re-runs the
 * entry).
 */

#include "jericho.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Register the unit: its config keys, its engine hooks and (later) its own
 * car-data source. Called from the module entry, or from another module's entry
 * once it is a module itself. */
void bake_register(JERICHO_CONTEXT* ctx);

#ifdef __cplusplus
}
#endif

#endif /* BAKE_H */

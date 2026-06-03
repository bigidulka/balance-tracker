import signerModules from "./debank-signer-modules.json";

type ModuleFactory = (module: { exports: unknown }, exports: Record<string, unknown>, require: RuntimeRequire) => void;

type RuntimeRequire = {
  (id: number): unknown;
  d: (exports: Record<string, unknown>, definition: Record<string, () => unknown>) => void;
  n: (module: unknown) => (() => unknown) & { a: () => unknown };
  g: typeof globalThis;
};

const moduleCache = new Map<number, { exports: unknown }>();

function createFactory(source: string): ModuleFactory {
  return new Function("module", "exports", "require", `return (${source})(module, exports, require);`) as ModuleFactory;
}

const factories = new Map<number, ModuleFactory>(
  Object.entries(signerModules).map(([id, source]) => [Number(id), createFactory(source)])
);

const runtimeRequire = ((id: number) => {
  if (moduleCache.has(id)) {
    return moduleCache.get(id)?.exports;
  }

  const factory = factories.get(id);
  if (!factory) {
    throw new Error(`Module ${id} not found in extracted signer runtime`);
  }

  const module = { exports: {} as Record<string, unknown> };
  moduleCache.set(id, module);
  factory(module, module.exports as Record<string, unknown>, runtimeRequire);
  return module.exports;
}) as RuntimeRequire;

runtimeRequire.d = (exports, definition) => {
  for (const key of Object.keys(definition)) {
    if (!Object.prototype.hasOwnProperty.call(exports, key)) {
      Object.defineProperty(exports, key, {
        enumerable: true,
        get: definition[key],
      });
    }
  }
};

runtimeRequire.n = (module) => {
  const getter = (() => module) as (() => unknown) & { a: () => unknown };
  getter.a = getter;
  return getter;
};

runtimeRequire.g = globalThis;

export function requireSignerModule<T>(id: number): T {
  return runtimeRequire(id) as T;
}

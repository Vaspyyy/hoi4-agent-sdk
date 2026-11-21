export interface SdkMeta {
  name: string;
  version: string;
  description: string;
  license: string;
  requiresPython: string;
  repository: string;
  issues: string;
  extras: Record<string, string[]>;
  exports: string[];
  stats: {
    runtimeDependencies: number;
    publicExports: number;
    modMethods: number;
    layerModules: number;
    definitionKinds: number;
    validationCodes: number;
    warningCodes: number;
    errorCodes: number;
    curatedEffects: number;
    curatedModifiers: number;
    techCategories: number;
    releases: number;
    testSuites: number;
    sourceModules: number;
    docsPages: number;
    unreleasedEntries: number;
  };
}

export interface ModMethodInfo {
  name: string;
  layer: string;
  layerClass: string;
  tier: string;
  kind: "method" | "staticmethod" | "classmethod" | "property";
  signature: string;
  summary: string;
  doc: string;
}

export interface LayerInfo {
  id: string;
  className: string;
  filePath: string;
  tier: "foundation" | "domains" | "validation" | "persistence";
  lines: number;
  description: string;
  directBases: string[];
  transitiveBases: string[];
  dependents: string[];
  methodCount: number;
  methods: ModMethodInfo[];
}

export interface LayersPayload {
  layers: LayerInfo[];
  methods: ModMethodInfo[];
}

export interface DefinitionKindInfo {
  name: string;
  directory: string;
  container: string | null;
  entryKey: string | null;
  idField: string;
  recursive: boolean;
  defaultFile: string;
  layout: string;
  description: string;
}

export interface ValidationCodeInfo {
  code: string;
  description: string;
  severity: "error" | "warning";
  suppressible: boolean;
  category: string;
}

export interface EffectCategory {
  category: string;
  items: { label: string; script: string }[];
}

export interface ModifierCategory {
  category: string;
  items: { label: string; key: string; example: string }[];
}

export interface CatalogsPayload {
  definitions: DefinitionKindInfo[];
  validationCodes: ValidationCodeInfo[];
  effects: EffectCategory[];
  modifiers: ModifierCategory[];
  technologyCategories: string[];
}

export interface ReleaseSection {
  title: string;
  items: string[];
}

export interface ReleaseEntry {
  version: string;
  id: string;
  date: string;
  isUnreleased: boolean;
  sections: ReleaseSection[];
  counts: {
    added: number;
    changed: number;
    fixed: number;
    removed: number;
    migration: number;
    other: number;
  };
  totalItems: number;
}

export interface DocHeading {
  level: number;
  title: string;
  id: string;
}

export interface DocPage {
  slug: string;
  title: string;
  group: string;
  summary: string;
  sourcePath: string;
  badge: string;
  lines: number;
  words: number;
  readingMinutes: number;
  headings: DocHeading[];
  markdown: string;
}

export interface SearchItem {
  id: string;
  kind: "Doc" | "Section" | "Method" | "Layer" | "Definition" | "Validation" | "Release";
  title: string;
  subtitle: string;
  href: string;
  keywords: string;
}

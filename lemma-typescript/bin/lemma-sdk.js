#!/usr/bin/env node

import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const SHADCN_SCHEMA_URL = "https://ui.shadcn.com/schema.json";
const LEMMA_REGISTRY_URL =
  "https://lemma-work.github.io/lemma-platform/r/{name}.json";

function printUsage() {
  console.log(`Usage:
  lemma-sdk init-shadcn

Commands:
  init-shadcn   Add the @lemma shadcn registry to components.json in the current directory.`);
}

function fail(message) {
  console.error(`lemma-sdk: ${message}`);
  process.exit(1);
}

function ensureObject(value, label) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    fail(`${label} must be a JSON object.`);
  }

  return value;
}

function loadComponentsConfig(configPath) {
  if (!existsSync(configPath)) {
    return {
      config: {
        $schema: SHADCN_SCHEMA_URL,
        registries: {},
      },
      existed: false,
    };
  }

  let parsed;
  try {
    parsed = JSON.parse(readFileSync(configPath, "utf8"));
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    fail(`Could not parse ${configPath}: ${message}`);
  }

  const config = ensureObject(parsed, "components.json");

  if (config.$schema == null) {
    config.$schema = SHADCN_SCHEMA_URL;
  }

  if (config.registries == null) {
    config.registries = {};
  }

  ensureObject(config.registries, "components.json registries");

  return {
    config,
    existed: true,
  };
}

function writeComponentsConfig(configPath, config) {
  writeFileSync(configPath, `${JSON.stringify(config, null, 2)}\n`, "utf8");
}

function initShadcn() {
  const configPath = resolve(process.cwd(), "components.json");
  const { config, existed } = loadComponentsConfig(configPath);

  const currentUrl = config.registries["@lemma"];
  const alreadyConfigured = currentUrl === LEMMA_REGISTRY_URL;

  config.registries["@lemma"] = LEMMA_REGISTRY_URL;
  writeComponentsConfig(configPath, config);

  if (alreadyConfigured) {
    console.log(`@lemma is already configured in ${configPath}`);
  } else if (existed) {
    console.log(`Added @lemma registry to ${configPath}`);
  } else {
    console.log(`Created ${configPath} with the @lemma registry`);
    console.log("If you have not initialized shadcn yet, run: npx shadcn@latest init");
  }

  console.log(`Registry URL: ${LEMMA_REGISTRY_URL}`);
}

const command = process.argv[2];

switch (command) {
  case "init-shadcn":
    initShadcn();
    break;
  case "-h":
  case "--help":
  case "help":
  case undefined:
    printUsage();
    break;
  default:
    fail(`Unknown command "${command}". Run "lemma-sdk --help" for usage.`);
}

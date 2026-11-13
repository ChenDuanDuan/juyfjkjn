#!/usr/bin/env python3
"""Helper for porting fragmentum to Minecraft 26.3 (Fabric) in CI.

Usage:  python3 port_tools.py all      -> fix sources, build, dump API info
        python3 port_tools.py fix      -> only patch sources
        python3 port_tools.py inspect  -> only dump API info

The patches are applied to the checked-in (1.20.1) source tree right before
Stonecutter generates the 26.3 sources. Nothing is committed back.
"""
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

SRC = Path("src/main/kotlin/dev/obscuria/fragmentum")
GRADLE_CMD = ["./gradlew", ":26.3-fabric:buildAndCollect",
              "--no-daemon", "--stacktrace", "--console=plain"]

# (file, old, new, replace_all)
EDITS = [
    # --- Holder<T> now requires non-null type arguments -------------------
    ("api/registry/Deferred.kt",
     "interface Deferred<T> : Supplier<T>, Lazy<T> {",
     "interface Deferred<T : Any> : Supplier<T>, Lazy<T> {", False),
    ("api/registry/Deferred.kt",
     "fun <V> `as`(): Deferred<V>",
     "fun <V : Any> `as`(): Deferred<V>", False),
    ("api/registry/Deferred.kt",
     "fun <T> create(supplier: () -> Holder<T>): Deferred<T> {",
     "fun <T : Any> create(supplier: () -> Holder<T>): Deferred<T> {", False),
    ("registry/DeferredImpl.kt",
     "internal class DeferredImpl<T>(",
     "internal class DeferredImpl<T : Any>(", False),
    ("registry/DeferredImpl.kt",
     "override fun <V> `as`(): Deferred<V> {",
     "override fun <V : Any> `as`(): Deferred<V> {", False),
    ("api/registry/Registrar.kt",
     "fun <T> createDataRegistry(",
     "fun <T : Any> createDataRegistry(", False),
    ("api/registry/Registrar.kt",
     "fun <T> createSyncedDataRegistry(",
     "fun <T : Any> createSyncedDataRegistry(", True),
    ("platform/fabric/FabricRegistrar.kt",
     "override fun <T> createDataRegistry(",
     "override fun <T : Any> createDataRegistry(", False),
    ("platform/fabric/FabricRegistrar.kt",
     "override fun <T> createSyncedDataRegistry(",
     "override fun <T : Any> createSyncedDataRegistry(", True),
    ("platform/fabric/FabricRegistrar.kt",
     "return Deferred.create { holder }",
     "@Suppress(\"UNCHECKED_CAST\")\n\t\t\treturn Deferred.create { holder as net.minecraft.core.Holder<T> }", False),

    # --- ClientTooltipComponent.getHeight now takes a Font ----------------
    ("client/CompositeClientTooltipComponentImpl.kt",
     "override fun getHeight(): Int {",
     "override fun getHeight(font: Font): Int {", False),
    ("client/CompositeClientTooltipComponentImpl.kt",
     "it.height", "it.getHeight(font)", True),

    # --- TagKey keeps location(); only ResourceKey was renamed ------------
    ("common/tags/TagPostProcessorsImpl.kt",
     "computeIfAbsent(tagKey.location())",
     "computeIfAbsent(tagKey.location ())", False),

    # --- Fabric API networking rename ------------------------------------
    ("platform/fabric/FabricNetworking-21.1.kt",
     "PayloadTypeRegistry.playS2C()",
     "PayloadTypeRegistry.clientboundPlay()", False),
    ("platform/fabric/FabricNetworking-21.1.kt",
     "PayloadTypeRegistry.playC2S()",
     "PayloadTypeRegistry.serverboundPlay()", False),
]

# (file, regex, replacement)
REGEX_EDITS = [
    # `//? 1.21.1` only matches exactly 1.21.1; 26.3 needs the same branch.
    ("registry/AssetRegistryImpl.kt",
     r"//\? 1\.21\.1\n(\s*)//val value",
     r"//? >=1.21.1\n\1//val value"),
]


def apply_fixes():
    print("=== Applying source fixes ===", flush=True)
    ok = miss = 0
    for rel, old, new, all_ in EDITS:
        path = SRC / rel
        if not path.exists():
            print(f"!! MISSING FILE {rel}")
            miss += 1
            continue
        text = path.read_text(encoding="utf-8")
        if old not in text:
            print(f"!! NOT FOUND in {rel}: {old[:60]!r}")
            miss += 1
            continue
        text = text.replace(old, new) if all_ else text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8")
        print(f"ok {rel}: {old[:50]!r}")
        ok += 1
    for rel, pattern, repl in REGEX_EDITS:
        path = SRC / rel
        text = path.read_text(encoding="utf-8")
        new_text, n = re.subn(pattern, repl, text)
        if n == 0:
            print(f"!! REGEX NOT FOUND in {rel}: {pattern!r}")
            miss += 1
            continue
        path.write_text(new_text, encoding="utf-8")
        print(f"ok {rel}: regex {pattern[:40]!r}")
        ok += 1
    print(f"=== fixes done: {ok} applied, {miss} missed ===", flush=True)


# ----------------------------------------------------------------------
# API inspection: prints signatures straight from the downloaded jars.
# ----------------------------------------------------------------------
# Classes whose full public signature we want to see (simple names).
SHOW = [
    "ClientTooltipComponent", "BlockEntityRendererProvider", "ChatFormatting",
    "PayloadTypeRegistry", "FabricRegistryBuilder", "DynamicRegistries",
    "PackLocationInfo", "PackMetadataResources", "TagKey",
    "NeoForgeConfigRegistry", "ForgeConfigRegistry", "ConfigRegistry",
    "ParticleFactoryRegistry", "ColorProviderRegistry",
    "EntityModelLayerRegistry", "ModelLayerRegistry",
    "GuiGraphicsExtractor", "GuiGraphics",
]
# Substrings: list every class whose name contains one of these.
FIND = [
    "GuiGraphics", "MultiBufferSource", "BlockAndTintGetter",
    "ClampedItemPropertyFunction", "ItemProperties", "ParticleFactoryRegistry",
    "ColorProviderRegistry", "ModelLayerRegistry", "ConfigRegistry",
    "PackMetadataResources", "RegistryBuilder", "TooltipComponent",
    "ParticleProviderRegistry",
]


def find_jars():
    roots = [Path.home() / ".gradle", Path(".gradle"), Path("versions")]
    seen = set()
    for root in roots:
        if not root.exists():
            continue
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith(".jar") and not f.endswith("-sources.jar"):
                    p = os.path.join(dirpath, f)
                    if p not in seen:
                        seen.add(p)
                        yield p


def javap_path():
    found = shutil.which("javap")
    if found:
        return found
    home = os.environ.get("JAVA_HOME")
    return os.path.join(home, "bin", "javap") if home else "javap"


def inspect_api():
    JAVAP = javap_path()
    print("\n=== API INSPECTION START ===", flush=True)
    index = {}  # fqcn -> jar
    for jar in find_jars():
        try:
            with zipfile.ZipFile(jar) as z:
                for name in z.namelist():
                    if name.endswith(".class") and "module-info" not in name:
                        index.setdefault(name[:-6].replace("/", "."), jar)
        except Exception:
            continue
    print(f"indexed {len(index)} classes", flush=True)

    for q in FIND:
        hits = sorted(c for c in index if q in c.split(".")[-1] and "$" not in c)
        print(f"\n-- classes containing '{q}': {len(hits)}")
        for c in hits[:15]:
            print("   ", c)

    done = set()
    for simple in SHOW:
        for fqcn, jar in sorted(index.items()):
            if fqcn.split(".")[-1] == simple and fqcn not in done:
                done.add(fqcn)
                print(f"\n##### {fqcn}")
                r = subprocess.run([JAVAP, "-public", "-cp", jar, fqcn],
                                   capture_output=True, text=True)
                lines = (r.stdout or r.stderr).splitlines()
                for line in lines[:90]:
                    print(line)
                if len(lines) > 90:
                    print(f"... ({len(lines) - 90} more lines)")
    # Pack.ResourcesSupplier is an inner class.
    for fqcn in ["net.minecraft.server.packs.repository.Pack$ResourcesSupplier",
                 "net.minecraft.server.packs.repository.Pack$Metadata"]:
        if fqcn in index:
            print(f"\n##### {fqcn}")
            r = subprocess.run([JAVAP, "-public", "-cp", index[fqcn], fqcn],
                               capture_output=True, text=True)
            print(r.stdout or r.stderr)
    print("=== API INSPECTION END ===", flush=True)


def build():
    print("=== Running Gradle ===", flush=True)
    return subprocess.call(GRADLE_CMD)


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    code = 0
    if cmd in ("fix", "all"):
        apply_fixes()
    if cmd == "all":
        code = build()
    if cmd in ("inspect", "all"):
        try:
            inspect_api()
        except Exception as e:  # never hide the build result
            print("inspection failed:", e)
    sys.exit(code)


if __name__ == "__main__":
    main()

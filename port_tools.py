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

    # --- TagKey keeps location(); only ResourceKey was renamed ------------
    ("common/tags/TagPostProcessorsImpl.kt",
     "computeIfAbsent(tagKey.location())",
     "computeIfAbsent(tagKey.location ())", False),

    # --- Fabric registry builder: createSimple -> create -------------------
    ("platform/fabric/FabricRegistrar.kt",
     "FabricRegistryBuilder.createSimple<T>(registryKey)",
     "FabricRegistryBuilder.create<T>(registryKey)", False),

    # --- Forge Config API Port v5 ----------------------------------------
    ("config/ConfigBuilderImpl.kt",
     "fuzs.forgeconfigapiport.fabric.api.neoforge.v4.NeoForgeConfigRegistry.INSTANCE",
     "fuzs.forgeconfigapiport.fabric.api.v5.ConfigRegistry.INSTANCE", False),

    # --- Pack.ResourcesSupplier has new methods ---------------------------
    ("common/resource/FragmentumLayer-21.1.kt",
     "override fun openPrimary(info: PackLocationInfo) = Resources(info, root)",
     "override fun openMetadata(location: PackLocationInfo): net.minecraft.server.packs.PackMetadataResources = Resources(location, root)", False),
    ("common/resource/FragmentumLayer-21.1.kt",
     "override fun openFull(info: PackLocationInfo, metadata: Pack.Metadata) = Resources(info, root)",
     "override fun openResources(location: PackLocationInfo, metadata: Pack.Metadata): java.util.stream.Stream<net.minecraft.server.packs.PackResources> = java.util.stream.Stream.of(Resources(location, root))", False),

    # --- ChatFormatting.getByName is gone in 26.x -------------------------
    ("common/tooltip/TooltipTags.kt",
     "val color = requireNotNull(ChatFormatting.getByName(arg)).color",
     "val color: Int? = when (arg.lowercase()) {\n"
     "                        \"black\" -> 0x000000\n"
     "                        \"dark_blue\" -> 0x0000AA\n"
     "                        \"dark_green\" -> 0x00AA00\n"
     "                        \"dark_aqua\" -> 0x00AAAA\n"
     "                        \"dark_red\" -> 0xAA0000\n"
     "                        \"dark_purple\" -> 0xAA00AA\n"
     "                        \"gold\" -> 0xFFAA00\n"
     "                        \"gray\" -> 0xAAAAAA\n"
     "                        \"dark_gray\" -> 0x555555\n"
     "                        \"blue\" -> 0x5555FF\n"
     "                        \"green\" -> 0x55FF55\n"
     "                        \"aqua\" -> 0x55FFFF\n"
     "                        \"red\" -> 0xFF5555\n"
     "                        \"light_purple\" -> 0xFF55FF\n"
     "                        \"yellow\" -> 0xFFFF55\n"
     "                        \"white\" -> 0xFFFFFF\n"
     "                        else -> null\n"
     "                    }", False),

    # --- Fabric API networking rename ------------------------------------
    ("platform/fabric/FabricNetworking-21.1.kt",
     "PayloadTypeRegistry.playS2C()",
     "PayloadTypeRegistry.clientboundPlay()", False),
    ("platform/fabric/FabricNetworking-21.1.kt",
     "PayloadTypeRegistry.playC2S()",
     "PayloadTypeRegistry.serverboundPlay()", False),
]

# Whole files that are rewritten for 26.3 (features Obscure Tooltips does not
# use are dropped: item properties, colour providers, block-entity renderers,
# particle and model-layer registration).
FILE_REPLACE = {}

FILE_REPLACE["api/client/ClientRegistrar.kt"] = """package dev.obscuria.fragmentum.api.client

import dev.obscuria.fragmentum.api.registry.Deferred
import net.minecraft.client.gui.screens.inventory.tooltip.ClientTooltipComponent
import net.minecraft.client.renderer.entity.EntityRendererProvider
import net.minecraft.world.entity.Entity
import net.minecraft.world.entity.EntityType
import net.minecraft.world.inventory.tooltip.TooltipComponent

interface ClientRegistrar {

\tfun <T : TooltipComponent> registerTooltipComponent(
\t\ttype: Class<T>,
\t\tfactory: (T) -> ClientTooltipComponent
\t)

\tfun <T : Entity> registerEntityRenderer(
\t\ttype: Deferred<EntityType<T>>,
\t\tprovider: EntityRendererProvider<T>
\t)
}
"""

FILE_REPLACE["platform/fabric/FabricClientRegistrar.kt"] = """package dev.obscuria.fragmentum.platform.fabric

//? fabric {
/*import dev.obscuria.fragmentum.api.client.ClientRegistrar
import dev.obscuria.fragmentum.api.registry.Deferred
import dev.obscuria.fragmentum.client.TooltipComponentRegistry
import net.fabricmc.fabric.api.client.rendering.v1.EntityRendererRegistry
import net.minecraft.client.gui.screens.inventory.tooltip.ClientTooltipComponent
import net.minecraft.client.renderer.entity.EntityRendererProvider
import net.minecraft.world.entity.Entity
import net.minecraft.world.entity.EntityType
import net.minecraft.world.inventory.tooltip.TooltipComponent
import org.jetbrains.annotations.ApiStatus

@ApiStatus.Internal
internal data class FabricClientRegistrar(val modid: String) : ClientRegistrar {

\toverride fun <T : TooltipComponent> registerTooltipComponent(
\t\ttype: Class<T>,
\t\tfactory: (T) -> ClientTooltipComponent
\t) {
\t\tTooltipComponentRegistry.register(type, factory)
\t}

\toverride fun <T : Entity> registerEntityRenderer(
\t\ttype: Deferred<EntityType<T>>,
\t\tprovider: EntityRendererProvider<T>
\t) {
\t\tEntityRendererRegistry.register(type.get(), provider)
\t}
}
*///?}
"""

FILE_REPLACE["client/CompositeClientTooltipComponentImpl.kt"] = """package dev.obscuria.fragmentum.client

import dev.obscuria.fragmentum.api.client.CompositeClientTooltipComponent
import dev.obscuria.fragmentum.api.common.tooltip.CompositeTooltipComponent
import net.minecraft.client.gui.Font
import net.minecraft.client.gui.GuiGraphicsExtractor
import net.minecraft.client.gui.screens.inventory.tooltip.ClientTooltipComponent
import org.jetbrains.annotations.ApiStatus

@ApiStatus.Internal
internal data class CompositeClientTooltipComponentImpl(
\toverride val components: List<ClientTooltipComponent>
) : CompositeClientTooltipComponent {

\toverride fun getHeight(font: Font): Int {
\t\treturn components.sumOf { it.getHeight(font) }
\t}

\toverride fun getWidth(font: Font): Int {
\t\treturn components.maxOf { it.getWidth(font) }
\t}

\toverride fun extractImage(
\t\tfont: Font,
\t\tx: Int,
\t\ty: Int,
\t\twidth: Int,
\t\theight: Int,
\t\tgraphics: GuiGraphicsExtractor
\t) {
\t\tvar offset = 0
\t\tcomponents.forEach {
\t\t\tit.extractImage(font, x, y + offset, width, height, graphics)
\t\t\toffset += it.getHeight(font)
\t\t}
\t}

\toverride fun extractText(
\t\tgraphics: GuiGraphicsExtractor,
\t\tfont: Font,
\t\tx: Int,
\t\ty: Int
\t) {
\t\tvar offset = 0
\t\tcomponents.forEach {
\t\t\tit.extractText(graphics, font, x, y + offset)
\t\t\toffset += it.getHeight(font)
\t\t}
\t}

\tcompanion object {

\t\tfun create(component: CompositeTooltipComponent): CompositeClientTooltipComponentImpl {
\t\t\tval components = component.components.filterNotNull().map(ClientTooltipComponent::create)
\t\t\treturn CompositeClientTooltipComponentImpl(components)
\t\t}

\t\tfun <T : ClientTooltipComponent> findFirst(type: Class<T>, components: List<ClientTooltipComponent?>): T? {
\t\t\treturn components.firstNotNullOfOrNull {
\t\t\t\twhen {
\t\t\t\t\ttype.isInstance(it) -> type.cast(it)
\t\t\t\t\tit is CompositeClientTooltipComponent -> findFirst(type, it.components)
\t\t\t\t\telse -> null
\t\t\t\t}
\t\t\t}
\t\t}
\t}
}
"""

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
    for rel, content in FILE_REPLACE.items():
        path = SRC / rel
        if not path.exists():
            print(f"!! MISSING FILE for replace {rel}")
            miss += 1
            continue
        path.write_text(content, encoding="utf-8")
        print(f"ok {rel}: file replaced")
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
# Classes whose public signature we want to see (simple names or full names).
SHOW = [
    "net.minecraft.client.gui.screens.inventory.tooltip.ClientTooltipComponent",
    "net.minecraft.client.renderer.blockentity.BlockEntityRendererProvider",
    "net.minecraft.ChatFormatting",
    "net.minecraft.server.packs.repository.Pack",
    "net.minecraft.server.packs.repository.Pack$ResourcesSupplier",
    "net.minecraft.server.packs.repository.Pack$Metadata",
    "net.minecraft.server.packs.PackResources",
    "net.minecraft.resources.Identifier",
    "net.minecraft.resources.ResourceKey",
    "net.minecraft.client.renderer.MultiBufferSource",
    "net.minecraft.client.renderer.block.BlockAndTintGetter",
    "net.minecraft.client.particle.ParticleProvider",
    "net.minecraft.client.renderer.entity.EntityRendererProvider",
    "net.fabricmc.fabric.api.client.particle.v1.ParticleProviderRegistry",
    "net.fabricmc.fabric.api.client.rendering.v1.EntityRendererRegistry",
    "net.fabricmc.fabric.api.client.rendering.v1.ClientTooltipComponentCallback",
    "net.minecraft.client.gui.Font",
    "net.minecraft.client.renderer.state.gui.GuiRenderState",
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
    index = {}  # fqcn -> [jars]
    for jar in find_jars():
        try:
            with zipfile.ZipFile(jar) as z:
                for name in z.namelist():
                    if name.endswith(".class") and "module-info" not in name:
                        index.setdefault(name[:-6].replace("/", "."), []).append(jar)
        except Exception:
            continue
    print(f"indexed {len(index)} classes", flush=True)

    # The real 26.3 Minecraft jar is the one that has GuiGraphicsExtractor.
    marker = index.get("net.minecraft.client.gui.GuiGraphicsExtractor", [])
    mc_jar = marker[0] if marker else None
    print("minecraft 26.3 jar:", mc_jar, flush=True)

    def pick(fqcn):
        jars = index.get(fqcn, [])
        if mc_jar and mc_jar in jars:
            return mc_jar
        return jars[0] if jars else None

    for fqcn in SHOW:
        jar = pick(fqcn)
        if jar is None:
            print(f"\n##### {fqcn}\n(not found in any jar)")
            continue
        print(f"\n##### {fqcn}   [{os.path.basename(jar)}]")
        r = subprocess.run([JAVAP, "-public", "-cp", jar, fqcn],
                           capture_output=True, text=True)
        lines = (r.stdout or r.stderr).splitlines()
        for line in lines[:120]:
            print(line)
        if len(lines) > 120:
            print(f"... ({len(lines) - 120} more lines)")
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

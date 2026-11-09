"""Pull components and warning signs out of messy marketplace text.

Deterministic, regex-based, conservative by design: when the text is ambiguous we pick the
cheaper interpretation (DDR4 over DDR5, SATA over NVMe) so the valuation errs low.
Text is normalized first (see watchlist.normalize): lowercase, hyphens -> spaces, and
commas / newlines -> " | " so we can reason about clauses like "1tb ssd | 2tb hdd".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from typing import Any

from ..watchlist import Watchlist, normalize

# ---------------------------------------------------------------------------- vocab
PREBUILT_WORDS = re.compile(
    r"\b(gaming (?:pc|computer|desktop|rig|tower|build|setup|system)|desktop (?:pc|computer)|pc build|custom (?:pc|build|built)"
    r"|full (?:build|pc|setup|system)|complete (?:pc|build|system)|whole (?:pc|setup)|battlestation|workstation"
    r"|tower pc|prebuilt|pre built|specs\s*:|high end pc|budget pc|streaming pc|editing pc|pc for sale|computer for sale)\b")
PREBUILT_BRANDS = re.compile(
    r"\b(alienware (?:aurora|r\d+)|hp omen|omen \d{2}l|legion (?:t\d|tower)|predator orion|rog strix g\d{2}(?:dk|cf)?\b|skytech"
    r"|cyberpower(?:pc)?|ibuypower|nzxt (?:player|h\d)|corsair vengeance i\d|starforge|maingear|origin pc|falcon northwest"
    r"|digital storm|abs (?:gladiator|master|stratos|legend)|yeyian|periphio|msi aegis|msi codex|msi mpg infinite|lenovo legion tower)\b")
LAPTOP_WORDS = re.compile(
    r"\b(laptop|notebook|macbook|chromebook|zephyrus|razer blade|legion (?:5|7|9|slim|pro)\b|omen (?:15|16|17)\b|predator helios"
    r"|tuf (?:gaming )?[af]1[5-7]|alienware (?:m|x)1[5-8]|msi (?:raider|titan|katana|stealth|vector|sword|pulse|thin)|rog strix (?:g|scar) ?1[5-8]"
    r"|nitro 5|nitro v|ideapad|thinkpad|surface|xps 1[3-7]|inspiron|pavilion|vivobook|zenbook|aero 1[5-7]|blade 1[4-8])\b")
WANTED_WORDS = re.compile(r"\b(iso|wtb|want(?:ed|ing)? to buy|looking (?:for|to buy)|in search of|wanted\s*:|will pay|buying)\b")
ACCESSORY_WORDS = re.compile(
    r"\b(backplate|bracket|shroud|riser|sag (?:bracket|support)|cables?|cooler only|fans? only|replacement fans?|heatsink only"
    r"|water ?block|box only|empty box|sticker|case only|psu only|power supply only|motherboard only|mount|vertical mount|anti sag)\b")
HARD_FLAGS = [
    (r"\bfor parts\b", "for parts"), (r"\bparts only\b", "parts only"), (r"\bnot working\b", "not working"),
    (r"\bdoes ?n[o']?t (?:work|boot|post|turn on|power on)\b", "not working"), (r"\bwon'?t (?:boot|post|turn on|power on)\b", "won't boot"),
    (r"\bno (?:display|signal|video|post|power)\b(?!\s*(?:issues?|problems?))", "no display/power"), (r"\bartifact", "artifacting"),
    (r"\bbroken\b", "broken"), (r"\bneeds? repair", "needs repair"), (r"\bwater damage", "water damage"),
    (r"\bbent pins?\b", "bent pins"), (r"\bdead\b", "dead"), (r"\bfried\b", "fried"), (r"\bboot ?loop", "boot loop"),
    (r"\bcracked\b", "cracked"), (r"\bblue ?screens?\b", "blue screens"), (r"\bbsod", "blue screens"),
    (r"\boverheat", "overheating"), (r"\bthermal (?:throttl|issue)", "thermal issues"),
    (r"\bno gpu\b(?!\s*(?:issues?|problems?))", "no gpu"), (r"\bwithout (?:a |the )?(?:gpu|graphics card|video card)\b", "no gpu"),
    (r"\b(?:gpu|graphics card) (?:not included|sold separately|removed)\b", "no gpu"),
    (r"\bno (?:cpu|processor)\b(?!\s*(?:issues?|problems?))", "no cpu"), (r"\bno (?:ram|memory)\b(?!\s*(?:issues?|problems?))", "no ram"),
    (r"\bno (?:ssd|storage|hard ?drive|drives?)\b(?!\s*(?:issues?|problems?))", "no storage"),
    (r"\bcase (?:only|and psu only)\b", "case only"), (r"\bempty case\b", "case only"),
]
SOFT_FLAGS = [
    (r"\bmin(?:ed|ing)\b", "mining history"), (r"\buntested\b", "untested"), (r"\bas[- ]is\b", "sold as-is"),
    (r"\bno returns?\b", "no returns"), (r"\brepast", "repasted"), (r"\bmodded\b", "modded"), (r"\bcoil whine\b", "coil whine"),
    (r"\bscratch", "cosmetic wear"), (r"\bdent", "cosmetic wear"), (r"\bmissing\b", "missing items"),
    (r"\bno box\b", "no box"), (r"\bopen box\b", "open box"), (r"\brefurb", "refurbished"), (r"\bloud fan", "loud fans"),
    (r"\bfan (?:rattle|noise|broken)", "fan issue"), (r"\bfirm\b", "price firm"), (r"\bno low ?ball", "price firm"),
    (r"\bor trade\b", "open to trade"), (r"\bobo\b", "OBO"), (r"\bnegotiable\b", "negotiable"), (r"\bmust go\b", "motivated seller"),
    (r"\bmoving\b", "motivated seller"), (r"\bneed(?:s)? (?:it )?gone\b", "motivated seller"), (r"\bno (?:windows|os)\b", "no OS"),
    (r"\bwindows not activated\b", "no OS"), (r"\bwater ?cool", "water cooled"), (r"\baio\b", "AIO cooler"),
    (r"\bnever mined\b", "seller says never mined"),
]
RAM_WORDS = re.compile(r"\b(ddr[345]|ram|memory|dimm|sodimm|so dimm|vengeance|g\.?skill|trident|ripjaws|flare|dominator|fury|crucial pro|teamgroup|t force|mhz|mt s|cl\d{2})\b")
SSD_WORDS = re.compile(r"\b(ssd|nvme|m\.?2|sata|storage|drive|gen ?[345]|pcie|990 pro|980 pro|970 evo|870 evo|860 evo|mx500|bx500|sn850x?|sn770|sn7100|sn580|sn570|nv[23]|p5 plus|p3 plus|t500|t700|kc3000|firecuda|wd black|wd blue|crucial|solidigm|lexar nm|hard ?drive|hdd)\b")
NVME_WORDS = re.compile(r"\b(nvme|m\.?2|gen ?[345]|pcie|990 pro|980 pro|970 evo|sn850x?|sn770|sn7100|p5 plus|p3 plus|t500|t700|kc3000|firecuda 5\d0|wd black|solidigm|lexar nm)\b")
HDD_WORDS = re.compile(r"\b(hdd|hard ?drive|barracuda|7200 ?rpm|5400 ?rpm|ironwolf|wd (?:blue|red|purple) (?:hdd|\d)|seagate (?:\d|hdd)|spinning|mechanical)\b")
VRAM_WORDS = re.compile(r"\b(vram|gddr\d?x?|graphics|video|gpu)\b")
SODIMM_RE = re.compile(r"\b(sodimm|so dimm|laptop ram|laptop memory)\b")
ECC_RE = re.compile(r"\b(ecc|rdimm|registered|server (?:ram|memory))\b")
GB_RE = re.compile(r"(?<![\d.])(\d{1,4})\s*gb\b")
KIT_RE = re.compile(r"(?<!\d)(\d)\s*x\s*(\d{1,3})\s*gb\b")
TB_RE = re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d)?)\s*tb\b")
SPEED_RE = re.compile(r"(?<!\d)([3-8]\d{3})\s*(?:mhz|mt s|mts|mt)\b")
CL_RE = re.compile(r"\bcl\s*(\d{2})\b")
RAM_CAPS = (4, 8, 12, 16, 24, 32, 48, 64, 96, 128, 192, 256)
SSD_GB_CAPS = {240: 256, 250: 256, 256: 256, 480: 512, 500: 512, 512: 512, 960: 1024, 1000: 1024, 1024: 1024, 2000: 2048, 2048: 2048, 4000: 4096}
PLAUSIBLE_VRAM: dict[str, set[int]] = {
    "rtx-3080": {10, 12}, "rtx-3060": {8, 12}, "rtx-4060-ti": {8, 16}, "rtx-5060-ti": {8, 16}, "rtx-3050": {6, 8},
}


@dataclass
class Component:
    category: str
    key: str
    label: str
    quantity: int = 1
    detail: dict[str, Any] = field(default_factory=dict)
    in_title: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Extraction:
    components: list[Component] = field(default_factory=list)
    kind: str = "single"              # single | bundle | prebuilt
    is_laptop: bool = False
    is_wanted_ad: bool = False
    is_accessory: bool = False
    hard_flags: list[str] = field(default_factory=list)
    soft_flags: list[str] = field(default_factory=list)
    ram_type_inferred: bool = False
    notes: list[str] = field(default_factory=list)
    method: str = "regex"

    def by_category(self, category: str) -> list[Component]:
        return [c for c in self.components if c.category == category]

    def categories(self) -> set[str]:
        return {c.category for c in self.components}

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------- helpers
def _unique(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _flags(text: str, table: list[tuple[str, str]]) -> list[str]:
    return _unique([label for pat, label in table if re.search(pat, text)])


def _segment_after(text: str, pos: int, limit: int = 40) -> str:
    """Text after pos up to the next clause separator."""
    chunk = text[pos: pos + limit]
    return chunk.split("|", 1)[0]


def _segment_before(text: str, pos: int, limit: int = 40) -> str:
    chunk = text[max(0, pos - limit): pos]
    return chunk.rsplit("|", 1)[-1]


def _clause(text: str, start: int, end: int) -> str:
    return _segment_before(text, start) + text[start:end] + _segment_after(text, end)


def _infer_ram_type(text: str, cpus: list[Component]) -> tuple[str | None, bool]:
    if "ddr5" in text:
        return "ddr5", False
    if "ddr4" in text:
        return "ddr4", False
    if "ddr3" in text:
        return "ddr3", False
    platforms = {c.detail.get("platform") for c in cpus}
    if "am5" in platforms or "lga1851" in platforms:
        return "ddr5", True
    if "am4" in platforms or "lga1700" in platforms:
        return "ddr4", True
    return None, True


def _ssd_key(capacity_gb: int, nvme: bool) -> str:
    fam = "nvme" if nvme else "sata-ssd"
    if capacity_gb >= 1024:
        return f"{fam}-{capacity_gb // 1024}tb"
    return f"{fam}-{capacity_gb}gb"


def _ram_key(capacity_gb: int, ram_type: str | None, sodimm: bool, ecc: bool) -> str:
    rt = ram_type or "ddr4"
    if ecc:
        return f"{rt}-ecc-rdimm-{capacity_gb}gb"
    if sodimm:
        return f"{rt}-sodimm-{capacity_gb}gb"
    return f"{rt}-{capacity_gb}gb"


def _is_vram_mention(cap: int, pos: int, gpu_spans: list[tuple[int, int, set[int]]], text: str) -> bool:
    """A 'NNgb' right after a GPU model that matches that GPU's VRAM is the card's memory, not system RAM."""
    seg_before = _segment_before(text, pos, 22)
    for _s, e, vram in gpu_spans:
        if e <= pos <= e + 16 and (cap in vram or not vram):
            return True
    if re.search(r"(gddr\d?x?|vram)\s*$", seg_before):
        return True
    if re.match(r"\s*(vram|gddr|of vram|video memory)", text[pos:pos + 20]):
        return True
    return False


# ---------------------------------------------------------------------------- main
def extract(title: str, description: str | None, watchlist: Watchlist) -> Extraction:
    title_n = normalize(title or "")
    desc_n = normalize(description or "")
    text = f"{title_n} | {desc_n}".strip(" |") if desc_n else title_n
    title_end = len(title_n)
    ex = Extraction()

    ex.is_wanted_ad = bool(WANTED_WORDS.search(title_n)) and not re.search(r"\bfor sale\b|\bselling\b", title_n)
    ex.hard_flags = _flags(text, HARD_FLAGS)
    ex.soft_flags = _flags(text, SOFT_FLAGS)
    prebuilt_words = bool(PREBUILT_WORDS.search(text) or PREBUILT_BRANDS.search(text))
    laptop_in_title = bool(LAPTOP_WORDS.search(title_n))
    ex.is_laptop = laptop_in_title or (bool(LAPTOP_WORDS.search(desc_n)) and not prebuilt_words)

    # -- GPUs and CPUs via watchlist patterns
    gpu_spans: list[tuple[int, int, set[int]]] = []
    hits: dict[str, Component] = {}
    for sku in watchlist.patterned():
        for pat in sku.patterns:
            m = pat.search(text)
            if not m:
                continue
            in_title = bool(pat.search(title_n))
            comp = hits.get(sku.key)
            if comp is None:
                comp = Component(sku.category, sku.key, sku.name, 1,
                                 {"platform": sku.platform, "vram_gb": sku.vram_gb, "pos": m.start()}, in_title)
                hits[sku.key] = comp
            else:
                comp.in_title = comp.in_title or in_title
            if sku.category == "gpu":
                vram = set(PLAUSIBLE_VRAM.get(sku.key, set()))
                if sku.vram_gb:
                    vram.add(int(sku.vram_gb))
                for mm in pat.finditer(text):
                    gpu_spans.append((mm.start(), mm.end(), vram))
            break

    gpus = sorted([c for c in hits.values() if c.category == "gpu"], key=lambda c: (not c.in_title, c.detail["pos"]))
    cpus = sorted([c for c in hits.values() if c.category == "cpu"], key=lambda c: (not c.in_title, c.detail["pos"]))

    # -- RAM
    sodimm = bool(SODIMM_RE.search(text)) or (ex.is_laptop and not prebuilt_words)
    ecc = bool(ECC_RE.search(text))
    ram_type, inferred = _infer_ram_type(text, cpus)
    ram_caps: list[tuple[int, int, bool]] = []  # (capacity, pos, in_title)
    kit_spans: list[tuple[int, int]] = []
    for m in KIT_RE.finditer(text):
        total = int(m.group(1)) * int(m.group(2))
        clause = _clause(text, m.start(), m.end())
        if HDD_WORDS.search(clause) or re.search(r"\b(ssd|nvme|tb)\b", _segment_after(text, m.end(), 15)):
            continue
        if RAM_WORDS.search(clause) or total in RAM_CAPS:
            ram_caps.append((total, m.start(), m.end() <= title_end))
            kit_spans.append((m.start(), m.end()))
    for m in GB_RE.finditer(text):
        cap = int(m.group(1))
        if cap not in RAM_CAPS or any(s <= m.start() < e for s, e in kit_spans):
            continue
        if _is_vram_mention(cap, m.start(), gpu_spans, text):
            continue
        after = _segment_after(text, m.end(), 20)
        if re.match(r"\s*(ssd|nvme|m\.?2|storage|hdd|drive|hard)", after):
            continue
        clause = _clause(text, m.start(), m.end())
        if SSD_WORDS.search(_segment_after(text, m.end(), 12)) and not RAM_WORDS.search(_segment_after(text, m.end(), 12)):
            continue
        if RAM_WORDS.search(clause) and not VRAM_WORDS.search(_segment_before(text, m.start(), 12)):
            ram_caps.append((cap, m.start(), m.end() <= title_end))
        elif prebuilt_words and cap in (8, 16, 32, 64) and not SSD_WORDS.search(clause):
            # bare "16gb" in a prebuilt listing with no other hint: almost always system RAM
            ram_caps.append((cap, m.start(), m.end() <= title_end))
    if ram_caps:
        caps = sorted({c for c, _, _ in ram_caps})
        if len(caps) > 1:
            ex.notes.append(f"multiple RAM capacities mentioned ({', '.join(f'{c}GB' for c in caps)}); using largest")
        cap = caps[-1]
        in_title = any(t for c, _, t in ram_caps if c == cap)
        key = _ram_key(cap, ram_type, sodimm, ecc)
        detail: dict[str, Any] = {"capacity_gb": cap, "type": ram_type, "inferred_type": inferred, "sodimm": sodimm, "ecc": ecc}
        sp = SPEED_RE.search(text)
        if sp:
            detail["speed"] = int(sp.group(1))
        cl = CL_RE.search(text)
        if cl:
            detail["cl"] = int(cl.group(1))
        ex.ram_type_inferred = inferred
        if inferred:
            ex.notes.append(f"RAM type not stated; assumed {ram_type or 'ddr4 (conservative)'}")
        label = f"{cap}GB {(ram_type or 'DDR4?').upper()}{' SODIMM' if sodimm else ''}{' ECC' if ecc else ''}"
        ex.components.append(Component("ram", key, label, 1, detail, in_title))

    # -- SSDs
    ssd_hits: list[tuple[int, bool, int, bool]] = []  # (capacity_gb, nvme, pos, in_title)

    def consider_drive(cap_gb: int, start: int, end: int) -> None:
        after = _segment_after(text, end, 30)
        before = _segment_before(text, start, 30)
        clause = before + text[start:end] + after
        first = re.match(r"\s*((?:\w+\.?\w*\s*){1,2})", after)
        head = first.group(1) if first else ""
        if HDD_WORDS.search(head) or HDD_WORDS.search(before[-14:]):
            return
        if not (SSD_WORDS.search(head) or SSD_WORDS.search(clause)
                or (SSD_WORDS.search(text) and not RAM_WORDS.search(clause))):
            return
        if RAM_WORDS.search(after[:10]) and not SSD_WORDS.search(after[:10]):
            return
        nvme = bool(NVME_WORDS.search(after) or NVME_WORDS.search(before[-16:]))
        ssd_hits.append((cap_gb, nvme, start, end <= title_end))

    for m in TB_RE.finditer(text):
        cap_tb = float(m.group(1))
        if cap_tb > 16:
            continue
        consider_drive(int(round(cap_tb * 1024)), m.start(), m.end())
    for m in GB_RE.finditer(text):
        cap = int(m.group(1))
        if cap not in SSD_GB_CAPS:
            continue
        consider_drive(SSD_GB_CAPS[cap], m.start(), m.end())
    seen_ssd: set[tuple[int, bool]] = set()
    for cap, nvme, pos, in_title in sorted(ssd_hits, key=lambda h: h[2]):
        if (cap, nvme) in seen_ssd:
            continue
        seen_ssd.add((cap, nvme))
        if len(seen_ssd) > 3:
            break
        label = f"{cap // 1024}TB" if cap >= 1024 and cap % 1024 == 0 else f"{cap}GB"
        ex.components.append(Component("ssd", _ssd_key(cap, nvme), f"{label} {'NVMe' if nvme else 'SATA?'} SSD", 1,
                                       {"capacity_gb": cap, "nvme": nvme}, in_title))

    # -- GPU / CPU selection (one each; title wins)
    def _pick(items: list[Component], what: str) -> list[Component]:
        if not items:
            return []
        titled = [c for c in items if c.in_title]
        if len(items) > 1:
            ex.notes.append(f"multiple {what}s mentioned ({', '.join(c.key for c in items)}); valuing {(titled or items)[0].key}")
        return [(titled or items)[0]]

    chosen_gpus = [] if "no gpu" in ex.hard_flags else _pick(gpus, "GPU")
    chosen_cpus = [] if "no cpu" in ex.hard_flags else _pick(cpus, "CPU")
    if "no ram" in ex.hard_flags:
        ex.components = [c for c in ex.components if c.category != "ram"]
    if "no storage" in ex.hard_flags:
        ex.components = [c for c in ex.components if c.category != "ssd"]
    ex.components = chosen_gpus + chosen_cpus + ex.components

    # -- kind
    cats = ex.categories()
    if prebuilt_words or len(cats) >= 3:
        ex.kind = "prebuilt"
    elif len(cats) == 2:
        ex.kind = "bundle"
    else:
        ex.kind = "single"
    if ex.kind == "prebuilt" and laptop_in_title:
        ex.is_laptop = True

    # "no gpu" etc. on a prebuilt are informational (we just don't count that part), not disqualifying
    informational = {"no gpu", "no cpu", "no ram", "no storage"}
    if ex.kind == "prebuilt":
        for f in [f for f in ex.hard_flags if f in informational]:
            ex.hard_flags.remove(f)
            ex.soft_flags.append(f)

    # laptop RAM / laptop SSD listings are parts we value (SODIMM keys); a laptop with a GPU/CPU is a machine we skip
    if ex.is_laptop and not (cats & {"gpu", "cpu"}) and ex.kind != "prebuilt":
        ex.is_laptop = False
    if "seller says never mined" in ex.soft_flags and "mining history" in ex.soft_flags:
        ex.soft_flags.remove("mining history")

    ex.is_accessory = (bool(ACCESSORY_WORDS.search(title_n)) and ex.kind == "single"
                       and not re.search(r"\bwith\b|\bincludes?\b|\bcomes with\b", title_n))
    if ex.is_accessory:
        ex.notes.append("title looks like an accessory, not the part itself")
    return ex

import pytest

from pcflip.valuation.extract import extract


def keys(ex):
    return sorted(c.key for c in ex.components)


# (title, description, expected component keys, expected kind)
CASES = [
    ("Gaming PC - RTX 4070 Super, Ryzen 7 7800X3D, 32GB DDR5, 2TB NVMe",
     "Specs: Ryzen 7 7800X3D, RTX 4070 Super 12GB, 32GB (2x16GB) DDR5 6000MHz CL30, 2TB Samsung 990 Pro NVMe, B650, 750W. $900 OBO",
     ["ddr5-32gb", "nvme-2tb", "rtx-4070-super", "ryzen-7800x3d"], "prebuilt"),
    ("RTX 3090 FE 24GB", "Works great, never mined. $950 firm", ["rtx-3090"], "single"),
    ("RTX 3090 Ti Suprim X", "", ["rtx-3090-ti"], "single"),
    ("rtx3090 founders", None, ["rtx-3090"], "single"),
    ("Corsair Vengeance 32GB DDR5 6000", "2x16GB CL36 kit", ["ddr5-32gb"], "single"),
    ("Samsung 990 Pro 2TB", "Barely used, 98% health", ["nvme-2tb"], "single"),
    ("Alienware Aurora R15 i9-13900KF RTX 4080 32GB", "32GB DDR5, 1TB SSD + 2TB HDD, liquid cooled",
     ["ddr5-32gb", "i9-13900k", "rtx-4080", "sata-ssd-1tb"], "prebuilt"),
    ("Gaming pc rtx 4060 ti 16gb", "Ryzen 5 5600x, 16gb ram 3200mhz, 1tb nvme, 500gb ssd, no gpu issues",
     ["ddr4-16gb", "nvme-1tb", "rtx-4060-ti-16gb", "ryzen-5600x", "sata-ssd-512gb"], "prebuilt"),
    ("Custom PC for sale no gpu", "i7 12700k 32gb ddr4 1tb nvme b660 650w", ["ddr4-32gb", "i7-12700k", "nvme-1tb"], "prebuilt"),
    ("RTX 4070 Ti Super and 7950X3D combo", "both for 1200", ["rtx-4070-ti-super", "ryzen-7950x3d"], "bundle"),
    ("Gaming PC", "rtx 3080 ti, i7 13700k, 64gb ram, 2 tb ssd, 4tb hdd. doesnt turn on, for parts",
     ["ddr4-64gb", "i7-13700k", "rtx-3080-ti", "sata-ssd-2tb"], "prebuilt"),
    ("Gaming PC i7 RTX 3070 16GB 1TB", None, ["ddr4-16gb", "rtx-3070"], "prebuilt"),
    ("RTX 3080 10GB EVGA FTW3", "10gb model, no issues", ["rtx-3080"], "single"),
    ("64GB (2x32GB) DDR5 5600 Kingston Fury", "laptop sodimm pulled from legion", ["ddr5-sodimm-64gb"], "single"),
    ("Dell Precision 32GB DDR4 ECC RDIMM 2666 x4", "server memory, 4 sticks", ["ddr4-ecc-rdimm-32gb"], "single"),
    ("Ryzen 7 7800X3D", "pulled from working build, with box", ["ryzen-7800x3d"], "single"),
    ("i7-14700K", "used 6 months", ["i7-14700k"], "single"),
    ("Intel Core Ultra 9 285K", "", ["core-ultra-9-285k"], "single"),
    ("G.Skill Trident Z5 64GB DDR5-6400", "2 x 32gb cl32", ["ddr5-64gb"], "single"),
    ("WD Black SN850X 4TB", "nvme gen4", ["nvme-4tb"], "single"),
    ("Crucial MX500 1TB", "2.5 sata ssd", ["sata-ssd-1tb"], "single"),
    ("RX 7900 XTX Sapphire Nitro", "24gb", ["rx-7900-xtx"], "single"),
    ("High end pc 9800x3d 5080", "64gb ddr5 6000 cl30, 2tb 990 pro, 2tb sn850x",
     ["ddr5-64gb", "nvme-2tb", "rtx-5080", "ryzen-9800x3d"], "prebuilt"),
    ("Skytech Chronos i5-13600K RTX 4070", "16GB DDR5 1TB NVMe", ["ddr5-16gb", "i5-13600k", "nvme-1tb", "rtx-4070"], "prebuilt"),
    ("RTX 4090 Gaming PC 7950X 64GB", "Upgraded from a 3080 to the 4090 last year. 64GB DDR5, 4TB NVMe",
     ["ddr5-64gb", "nvme-4tb", "rtx-4090", "ryzen-7950x"], "prebuilt"),
]


@pytest.mark.parametrize("title,desc,expected,kind", CASES, ids=[c[0][:40] for c in CASES])
def test_components_and_kind(watchlist, title, desc, expected, kind):
    ex = extract(title, desc, watchlist)
    assert keys(ex) == expected
    assert ex.kind == kind


def test_wanted_ad(watchlist):
    ex = extract("ISO RTX 4090", "looking for a 4090 cash in hand", watchlist)
    assert ex.is_wanted_ad
    assert not extract("RTX 4090 for sale", "", watchlist).is_wanted_ad


def test_accessory(watchlist):
    assert extract("RTX 3090 backplate", "custom backplate only", watchlist).is_accessory
    assert not extract("RTX 3090 with original box and cables", "", watchlist).is_accessory


def test_laptop_detection(watchlist):
    ex = extract("ASUS ROG Zephyrus G14 RTX 4060 16GB RAM 1TB", "2023 model laptop", watchlist)
    assert ex.is_laptop
    # laptop RAM is a part we value, not a laptop
    ex2 = extract("64GB (2x32GB) DDR5 5600 Kingston Fury", "laptop sodimm", watchlist)
    assert not ex2.is_laptop and keys(ex2) == ["ddr5-sodimm-64gb"]


def test_flags(watchlist):
    ex = extract("Gaming PC", "rtx 3080 ti, doesnt turn on, for parts. mined for a year", watchlist)
    assert "for parts" in ex.hard_flags and "not working" in ex.hard_flags
    assert "mining history" in ex.soft_flags
    ex2 = extract("RTX 3090", "never mined, no issues", watchlist)
    assert "mining history" not in ex2.soft_flags
    assert "seller says never mined" in ex2.soft_flags
    ex3 = extract("Gaming pc", "rtx 3070, no gpu issues, no display problems", watchlist)
    assert ex3.hard_flags == []


def test_no_gpu_prebuilt_is_informational(watchlist):
    ex = extract("Custom PC no gpu", "i7 12700k 32gb ddr4 1tb nvme", watchlist)
    assert "no gpu" in ex.soft_flags and "no gpu" not in ex.hard_flags
    assert "gpu" not in ex.categories()


def test_vram_not_counted_as_ram(watchlist):
    ex = extract("RTX 4060 Ti 16GB", "16gb vram model", watchlist)
    assert keys(ex) == ["rtx-4060-ti-16gb"]
    ex2 = extract("RTX 3080 12GB", "12gb version", watchlist)
    assert keys(ex2) == ["rtx-3080"]


def test_ram_speed_not_a_cpu(watchlist):
    ex = extract("DDR5 7600 32GB kit", "g.skill 7600mhz", watchlist)
    assert keys(ex) == ["ddr5-32gb"]
    ex2 = extract("Ryzen 5 7600", "cpu only", watchlist)
    assert keys(ex2) == ["ryzen-7600x"]


def test_ram_type_inference(watchlist):
    ex = extract("Gaming pc 7700x 32gb", "rtx 4070, 1tb nvme", watchlist)
    ram = ex.by_category("ram")[0]
    assert ram.key == "ddr5-32gb" and ram.detail["inferred_type"] is True
    ex2 = extract("Gaming pc 5800x3d 32gb", "rtx 3080", watchlist)
    assert ex2.by_category("ram")[0].key == "ddr4-32gb"

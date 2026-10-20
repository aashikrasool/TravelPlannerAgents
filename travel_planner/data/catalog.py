"""Offline place catalog (acts as the local "places API").

Prices are approximate per-person figures in KRW (2025-26 levels) and are meant for planning,
not for booking. Cities that are not in this catalog are looked up live on OpenStreetMap.

Attraction tuple: (id, name, tags, indoor, cost, hours, lat, lon, area, popularity, description)
Restaurant tuple: (id, name, tags, cost, meal, lat, lon, area, popularity)
"""
from __future__ import annotations

A = "attraction"
R = "restaurant"

CITIES: dict[str, dict] = {
    "busan": {
        "name": "Busan", "lat": 35.1796, "lon": 129.0756,
        "aliases": ["부산", "pusan"],
        "transit_per_day": 6000,
        "breakfast": {"normal": 8000, "budget": 4500},
        "attractions": [
            ("bs_haeundae", "Haeundae Beach", ["beach", "nature", "view"], False, 0, 1.5, 35.1587, 129.1604, "Haeundae", 5, "Busan's most famous beach with a long promenade."),
            ("bs_blueline", "Haeundae Blueline Park Sky Capsule", ["view", "family", "beach"], False, 17500, 1.5, 35.1611, 129.1735, "Haeundae", 4, "Tiny rail capsules along the coast from Mipo to Cheongsapo."),
            ("bs_gamcheon", "Gamcheon Culture Village", ["culture", "art", "photo"], False, 0, 2.0, 35.0975, 129.0106, "Saha", 5, "Colourful hillside village of murals and small galleries."),
            ("bs_jagalchi", "Jagalchi Fish Market", ["food", "market", "culture"], True, 0, 1.5, 35.0966, 129.0306, "Nampo", 5, "Korea's largest seafood market; buy and eat fresh fish upstairs."),
            ("bs_gukje", "Gukje Market & BIFF Square", ["food", "market", "shopping"], False, 0, 1.5, 35.1003, 129.0280, "Nampo", 4, "Sprawling market lanes and famous street-food square."),
            ("bs_tower", "Busan Tower (Yongdusan Park)", ["view", "city"], True, 12000, 1.0, 35.1007, 129.0324, "Nampo", 4, "Observation deck over the harbour."),
            ("bs_haedong", "Haedong Yonggungsa Temple", ["culture", "history", "view"], False, 0, 1.5, 35.1884, 129.2233, "Gijang", 5, "Seaside Buddhist temple on the rocks."),
            ("bs_sealife", "SEA LIFE Busan Aquarium", ["family", "animals"], True, 32000, 2.0, 35.1595, 129.1610, "Haeundae", 4, "Large aquarium under Haeundae Beach."),
            ("bs_museum", "Busan Museum", ["history", "culture"], True, 0, 1.5, 35.1290, 129.0927, "Nam-gu", 3, "Free museum on the city's history."),
            ("bs_moa", "Busan Museum of Art", ["art", "culture"], True, 0, 1.5, 35.1665, 129.1370, "Centum", 3, "Contemporary art museum, free entry."),
            ("bs_spaland", "Spa Land Centum City", ["relax", "wellness"], True, 23000, 2.5, 35.1690, 129.1313, "Centum", 4, "Hot-spring spa inside the world's largest department store."),
            ("bs_shinsegae", "Shinsegae Centum City", ["shopping"], True, 0, 2.0, 35.1688, 129.1295, "Centum", 3, "Huge department store and mall."),
            ("bs_taejongdae", "Taejongdae Park", ["nature", "hiking", "view"], False, 4000, 2.0, 35.0532, 129.0870, "Yeongdo", 4, "Cliff-top park with forest trails and lighthouse."),
            ("bs_igidae", "Igidae Coastal Walk", ["nature", "hiking", "view"], False, 0, 2.0, 35.1215, 129.1235, "Nam-gu", 4, "Coastal hiking trail to the Oryukdo Skywalk."),
            ("bs_gwangalli", "Gwangalli Beach & Gwangan Bridge night view", ["beach", "view", "nightlife", "evening"], False, 0, 1.5, 35.1532, 129.1187, "Suyeong", 5, "Lit-up bridge views and beach bars."),
            ("bs_cablecar", "Songdo Marine Cable Car", ["view", "family"], False, 17000, 1.5, 35.0763, 129.0237, "Seo-gu", 4, "Cable car over the sea at Songdo Beach."),
            ("bs_huinnyeoul", "Huinnyeoul Culture Village", ["culture", "art", "view"], False, 0, 1.5, 35.0784, 129.0444, "Yeongdo", 3, "Cliffside village with sea views."),
            ("bs_cinema", "Busan Cinema Center", ["art", "culture"], True, 0, 1.0, 35.1712, 129.1271, "Centum", 3, "Home of the Busan International Film Festival."),
            ("bs_seomyeon", "Seomyeon Food Alley evening crawl", ["food", "nightlife", "evening"], False, 0, 1.5, 35.1578, 129.0594, "Seomyeon", 4, "Busy alleys of pork-soup and street-food stalls."),
            ("bs_bujeon", "Bujeon Market", ["food", "market"], True, 0, 1.0, 35.1626, 129.0600, "Seomyeon", 3, "Large covered local market."),
            ("bs_maritime", "National Maritime Museum", ["history", "family"], True, 0, 2.0, 35.0786, 129.0806, "Yeongdo", 4, "Free museum on Korea's maritime history."),
        ],
        "restaurants": [
            ("bsr_gukbap", "Dwaeji-gukbap (pork rice soup) in Seomyeon", ["food", "local"], 11000, "any", 35.1570, 129.0600, "Seomyeon", 5),
            ("bsr_milmyeon", "Busan milmyeon (cold wheat noodles)", ["food", "local"], 10000, "lunch", 35.1000, 129.0300, "Nampo", 4),
            ("bsr_hoe", "Raw fish (hoe) at Jagalchi Market", ["food", "seafood"], 35000, "dinner", 35.0966, 129.0306, "Nampo", 5),
            ("bsr_eomuk", "Busan fish-cake (eomuk) bakery", ["food", "local"], 8000, "lunch", 35.0980, 129.0330, "Nampo", 4),
            ("bsr_street", "BIFF Square street food (ssiat hotteok, tteokbokki)", ["food", "street"], 7000, "lunch", 35.0990, 129.0270, "Nampo", 4),
            ("bsr_galbi", "Grilled pork galbi in Haeundae", ["food", "bbq"], 25000, "dinner", 35.1630, 129.1600, "Haeundae", 4),
            ("bsr_sogogi", "Beef rice soup at Haeundae Market", ["food", "local"], 10000, "any", 35.1627, 129.1597, "Haeundae", 4),
            ("bsr_clams", "Grilled clams (jogae-gui) at Cheongsapo", ["food", "seafood"], 30000, "dinner", 35.1600, 129.1920, "Haeundae", 4),
            ("bsr_nakgopsae", "Nakgopsae (octopus, tripe & shrimp stew)", ["food", "local"], 14000, "any", 35.1570, 129.0580, "Seomyeon", 4),
            ("bsr_chicken", "Fried chicken & beer at Gwangalli", ["food", "nightlife"], 18000, "dinner", 35.1540, 129.1190, "Suyeong", 3),
        ],
        "hotels": [
            ("bsh_gh", "Nampo Harbor Guesthouse", 1, 35000, 35.0990, 129.0310),
            ("bsh_budget", "Seomyeon Business Hotel", 2, 58000, 35.1580, 129.0590),
            ("bsh_mid", "Haeundae Ocean View Hotel", 3, 95000, 35.1610, 129.1620),
            ("bsh_up", "Haeundae Grand Resort", 4, 210000, 35.1590, 129.1680),
        ],
    },
    "seoul": {
        "name": "Seoul", "lat": 37.5665, "lon": 126.9780,
        "aliases": ["서울"],
        "transit_per_day": 7000,
        "breakfast": {"normal": 9000, "budget": 5000},
        "attractions": [
            ("se_gyeongbok", "Gyeongbokgung Palace", ["history", "culture"], False, 3000, 2.0, 37.5796, 126.9770, "Jongno", 5, "The grandest Joseon palace; changing of the guard."),
            ("se_bukchon", "Bukchon Hanok Village", ["culture", "photo"], False, 0, 1.5, 37.5826, 126.9830, "Jongno", 5, "Traditional hanok houses on hillside lanes."),
            ("se_insadong", "Insadong", ["culture", "shopping", "art"], False, 0, 1.5, 37.5740, 126.9850, "Jongno", 4, "Tea houses, crafts and galleries."),
            ("se_nmk", "National Museum of Korea", ["history", "culture", "family"], True, 0, 2.5, 37.5240, 126.9803, "Yongsan", 5, "Free national museum of Korean history and art."),
            ("se_tower", "N Seoul Tower", ["view", "city"], True, 21000, 1.5, 37.5512, 126.9882, "Namsan", 5, "Observation tower on Namsan mountain."),
            ("se_myeongdong", "Myeongdong shopping street", ["shopping", "food"], False, 0, 2.0, 37.5636, 126.9860, "Jung-gu", 4, "Cosmetics shops and evening street food."),
            ("se_gwangjang", "Gwangjang Market", ["food", "market"], True, 0, 1.5, 37.5700, 127.0099, "Jongno", 5, "Historic food market famous for bindaetteok."),
            ("se_ddp", "Dongdaemun Design Plaza", ["art", "architecture"], True, 0, 1.5, 37.5665, 127.0092, "Jung-gu", 4, "Zaha Hadid landmark with exhibitions."),
            ("se_changdeok", "Changdeokgung Palace & Secret Garden", ["history", "culture", "nature"], False, 8000, 2.0, 37.5794, 126.9910, "Jongno", 4, "UNESCO palace with a hidden royal garden."),
            ("se_bukhansan", "Bukhansan National Park hike", ["nature", "hiking"], False, 0, 4.0, 37.6586, 126.9780, "Gangbuk", 4, "Granite peaks right on the city's edge."),
            ("se_hangang", "Yeouido Hangang Park", ["nature", "relax"], False, 0, 1.5, 37.5284, 126.9340, "Yeouido", 4, "Riverside park, bikes and picnics."),
            ("se_seoulsky", "Seoul Sky (Lotte World Tower)", ["view"], True, 31000, 1.5, 37.5126, 127.1025, "Jamsil", 4, "One of the world's highest observation decks."),
            ("se_coex", "COEX Mall & Starfield Library", ["shopping"], True, 0, 2.0, 37.5117, 127.0592, "Gangnam", 4, "Underground mall with a giant library atrium."),
            ("se_mmca", "MMCA Seoul", ["art"], True, 2000, 1.5, 37.5788, 126.9800, "Jongno", 4, "National Museum of Modern and Contemporary Art."),
            ("se_hongdae", "Hongdae street music & nightlife", ["nightlife", "shopping", "music", "evening"], False, 0, 2.0, 37.5563, 126.9220, "Mapo", 4, "Buskers, clubs and indie shops."),
            ("se_cheonggye", "Cheonggyecheon Stream walk", ["nature", "city", "evening"], False, 0, 1.0, 37.5690, 126.9780, "Jung-gu", 3, "Restored stream through downtown."),
            ("se_lotteworld", "Lotte World Adventure", ["family", "theme_park"], True, 62000, 5.0, 37.5111, 127.0980, "Jamsil", 4, "Indoor theme park."),
            ("se_warmemorial", "War Memorial of Korea", ["history"], True, 0, 2.0, 37.5365, 126.9770, "Yongsan", 4, "Free museum on Korean military history."),
        ],
        "restaurants": [
            ("ser_bindae", "Gwangjang Market bindaetteok & mayak gimbap", ["food", "market"], 9000, "lunch", 37.5700, 127.0099, "Jongno", 5),
            ("ser_samgyeop", "Korean BBQ (samgyeopsal) in Mapo", ["food", "bbq"], 22000, "dinner", 37.5500, 126.9200, "Mapo", 5),
            ("ser_kalguksu", "Myeongdong kalguksu noodles", ["food", "local"], 11000, "lunch", 37.5630, 126.9850, "Jung-gu", 4),
            ("ser_samgyetang", "Ginseng chicken soup (samgyetang)", ["food", "local"], 20000, "any", 37.5780, 126.9720, "Jongno", 4),
            ("ser_nogari", "Euljiro nogari alley & beer", ["food", "nightlife"], 15000, "dinner", 37.5660, 126.9910, "Jung-gu", 4),
            ("ser_bibimbap", "Bibimbap in Insadong", ["food", "local"], 13000, "lunch", 37.5740, 126.9860, "Jongno", 4),
            ("ser_chimaek", "Fried chicken & beer (chimaek) in Hongdae", ["food", "nightlife"], 20000, "dinner", 37.5560, 126.9230, "Mapo", 4),
            ("ser_hanjeongsik", "Hanjeongsik (Korean course meal)", ["food", "fine_dining"], 45000, "dinner", 37.5750, 126.9840, "Jongno", 4),
            ("ser_dakgalbi", "Spicy stir-fried chicken (dakgalbi)", ["food", "local"], 15000, "any", 37.5600, 126.9370, "Sinchon", 4),
        ],
        "hotels": [
            ("seh_gh", "Jongno Hanok Guesthouse", 1, 45000, 37.5800, 126.9850),
            ("seh_budget", "Myeongdong Stay Hotel", 2, 75000, 37.5630, 126.9840),
            ("seh_mid", "City Hall Boutique Hotel", 3, 130000, 37.5660, 126.9780),
            ("seh_up", "Namsan Grand Hotel", 4, 280000, 37.5550, 126.9900),
        ],
    },
    "jeju": {
        "name": "Jeju", "lat": 33.4996, "lon": 126.5312,
        "aliases": ["제주", "jeju island", "jeju-do"],
        "transit_per_day": 9000,
        "breakfast": {"normal": 9000, "budget": 5000},
        "attractions": [
            ("je_seongsan", "Seongsan Ilchulbong (Sunrise Peak)", ["nature", "hiking", "view"], False, 5000, 2.0, 33.4581, 126.9425, "Seongsan", 5, "UNESCO tuff cone with a crater summit."),
            ("je_manjanggul", "Manjanggul Lava Tube", ["nature", "geology"], True, 4000, 1.5, 33.5283, 126.7714, "Gujwa", 4, "Underground lava cave, great on rainy days."),
            ("je_hallasan", "Hallasan hike (Eorimok trail)", ["nature", "hiking"], False, 0, 5.0, 33.3925, 126.4950, "Hallasan", 5, "Trail up Korea's highest mountain."),
            ("je_hyeopjae", "Hyeopjae Beach", ["beach", "nature"], False, 0, 1.5, 33.3940, 126.2396, "Hallim", 5, "Turquoise water facing Biyangdo island."),
            ("je_dongmun", "Dongmun Market", ["food", "market"], True, 0, 1.5, 33.5125, 126.5280, "Jeju-si", 4, "Traditional market with a night food market."),
            ("je_osulloc", "O'sulloc Tea Museum", ["food", "culture"], True, 0, 1.5, 33.3059, 126.2895, "Andeok", 4, "Green-tea fields, museum and desserts."),
            ("je_cheonjiyeon", "Cheonjiyeon Waterfall", ["nature", "view", "evening"], False, 2000, 1.0, 33.2470, 126.5545, "Seogwipo", 4, "Waterfall in a lush gorge, lit at night."),
            ("je_jeongbang", "Jeongbang Waterfall", ["nature"], False, 2000, 1.0, 33.2449, 126.5719, "Seogwipo", 3, "Waterfall dropping straight into the sea."),
            ("je_arte", "ARTE MUSEUM Jeju", ["art", "family"], True, 17000, 1.5, 33.3966, 126.3450, "Aewol", 4, "Immersive digital art exhibition."),
            ("je_museum", "Jeju National Museum", ["history", "culture"], True, 0, 1.5, 33.5136, 126.5490, "Jeju-si", 3, "History of Tamna and Jeju island."),
            ("je_olle7", "Jeju Olle Trail Route 7 walk", ["nature", "hiking", "view"], False, 0, 3.0, 33.2400, 126.5300, "Seogwipo", 4, "Coastal walking trail past sea cliffs."),
            ("je_aewol", "Aewol coastal cafe street", ["food", "view", "relax"], False, 0, 1.5, 33.4630, 126.3100, "Aewol", 4, "Cafes on the sea-view coastal road."),
            ("je_haenyeo", "Haenyeo Museum", ["culture", "history"], True, 1100, 1.0, 33.5233, 126.8620, "Gujwa", 3, "Story of Jeju's women free-divers."),
            ("je_udo", "Udo Island day trip (ferry)", ["nature", "beach", "view"], False, 10500, 4.0, 33.5040, 126.9530, "Udo", 4, "Small island with white-sand beaches."),
            ("je_olle_market", "Seogwipo Maeil Olle Market", ["food", "market", "evening"], True, 0, 1.0, 33.2500, 126.5630, "Seogwipo", 4, "Covered market with street food."),
        ],
        "restaurants": [
            ("jer_blackpork", "Jeju black pork BBQ (heukdwaeji)", ["food", "bbq"], 28000, "dinner", 33.5110, 126.5250, "Jeju-si", 5),
            ("jer_gogiguksu", "Gogi-guksu (pork noodle soup)", ["food", "local"], 10000, "lunch", 33.5000, 126.5300, "Jeju-si", 4),
            ("jer_ttukbaegi", "Seafood stew (haemul-ttukbaegi)", ["food", "seafood"], 15000, "any", 33.2480, 126.5620, "Seogwipo", 4),
            ("jer_galchi", "Grilled hairtail (galchi-gui)", ["food", "seafood"], 35000, "dinner", 33.2470, 126.5600, "Seogwipo", 4),
            ("jer_jeonbok", "Abalone porridge (jeonbok-juk)", ["food", "seafood"], 15000, "lunch", 33.4590, 126.9300, "Seongsan", 4),
            ("jer_tangerine", "Tangerine cafe & hallabong snacks", ["food", "cafe"], 8000, "lunch", 33.3900, 126.2500, "Hallim", 3),
            ("jer_hoe", "Raw fish & seafood platter by the harbor", ["food", "seafood"], 40000, "dinner", 33.5170, 126.5260, "Jeju-si", 4),
        ],
        "hotels": [
            ("jeh_gh", "Jeju Olle Guesthouse", 1, 38000, 33.5000, 126.5300),
            ("jeh_budget", "Jeju City Hotel", 2, 65000, 33.4990, 126.5310),
            ("jeh_mid", "Seogwipo Ocean Hotel", 3, 120000, 33.2460, 126.5620),
            ("jeh_up", "Jungmun Resort", 4, 260000, 33.2450, 126.4110),
        ],
    },
    "gyeongju": {
        "name": "Gyeongju", "lat": 35.8562, "lon": 129.2247,
        "aliases": ["경주", "kyongju"],
        "transit_per_day": 6000,
        "breakfast": {"normal": 8000, "budget": 4500},
        "attractions": [
            ("gj_bulguksa", "Bulguksa Temple", ["history", "culture"], False, 0, 2.0, 35.7900, 129.3320, "Bulguksa", 5, "UNESCO-listed Silla-era temple."),
            ("gj_seokguram", "Seokguram Grotto", ["history", "culture", "hiking"], False, 0, 1.5, 35.7950, 129.3490, "Bulguksa", 4, "Stone Buddha grotto reached by a forest path."),
            ("gj_daereungwon", "Daereungwon Tomb Complex", ["history"], False, 3000, 1.5, 35.8380, 129.2130, "Downtown", 5, "Grassy royal burial mounds."),
            ("gj_cheomseong", "Cheomseongdae Observatory", ["history"], False, 0, 0.5, 35.8347, 129.2190, "Downtown", 4, "Asia's oldest surviving observatory."),
            ("gj_donggung", "Donggung Palace & Wolji Pond at night", ["history", "view", "nightlife", "evening"], False, 3000, 1.5, 35.8346, 129.2266, "Downtown", 5, "Palace pavilions reflected in the pond."),
            ("gj_museum", "Gyeongju National Museum", ["history", "culture"], True, 0, 2.0, 35.8290, 129.2280, "Downtown", 5, "Silla gold crowns and the Divine Bell."),
            ("gj_hwangnidan", "Hwangnidan-gil", ["food", "shopping", "culture"], False, 0, 1.5, 35.8370, 129.2100, "Downtown", 4, "Hanok cafes and boutiques."),
            ("gj_woljeonggyo", "Woljeonggyo Bridge", ["history", "view", "evening"], False, 0, 0.5, 35.8290, 129.2180, "Downtown", 4, "Reconstructed covered bridge, lit at night."),
            ("gj_namsan", "Gyeongju Namsan hike", ["nature", "hiking", "history"], False, 0, 3.0, 35.7800, 129.2200, "Namsan", 3, "Mountain trails lined with Buddhist carvings."),
            ("gj_bomun", "Bomun Lake", ["nature", "relax"], False, 0, 1.5, 35.8420, 129.2850, "Bomun", 3, "Lakeside promenade and cherry trees."),
            ("gj_world", "Gyeongju World", ["family", "theme_park"], False, 48000, 4.0, 35.8370, 129.2800, "Bomun", 3, "Theme park with roller coasters."),
            ("gj_craft", "Gyeongju Folk Craft Village", ["culture", "art"], True, 0, 1.0, 35.7930, 129.3170, "Bulguksa", 2, "Pottery and craft workshops."),
        ],
        "restaurants": [
            ("gjr_ssambap", "Ssambap (leaf-wrap rice set)", ["food", "local"], 15000, "any", 35.8360, 129.2150, "Downtown", 5),
            ("gjr_bread", "Gyeongju bread & chalboribbang", ["food", "snack"], 6000, "lunch", 35.8380, 129.2120, "Downtown", 4),
            ("gjr_tteokgalbi", "Grilled short-rib patties (tteokgalbi)", ["food", "bbq"], 20000, "dinner", 35.8350, 129.2200, "Downtown", 4),
            ("gjr_brunch", "Hwangnidan-gil hanok cafe brunch", ["food", "cafe"], 14000, "lunch", 35.8370, 129.2100, "Downtown", 4),
            ("gjr_haejang", "Haejangguk (beef & veg soup)", ["food", "local"], 9000, "any", 35.8400, 129.2150, "Downtown", 4),
            ("gjr_hanu", "Korean beef (hanu) BBQ", ["food", "bbq", "fine_dining"], 45000, "dinner", 35.8420, 129.2180, "Downtown", 4),
        ],
        "hotels": [
            ("gjh_gh", "Hwangnam Hanok Guesthouse", 1, 40000, 35.8370, 129.2110),
            ("gjh_budget", "Gyeongju Station Hotel", 2, 60000, 35.8440, 129.2160),
            ("gjh_mid", "Bomun Lake Hotel", 3, 110000, 35.8420, 129.2850),
            ("gjh_up", "Bomun Grand Resort", 4, 230000, 35.8430, 129.2870),
        ],
    },
}

# Round-trip-ready one-way intercity options: (mode, name, cost per person, hours)
INTERCITY = {
    ("seoul", "busan"): [("train", "KTX high-speed train", 59800, 2.7), ("train", "SRT train", 52600, 2.6),
                          ("bus", "Express bus", 34200, 4.3), ("flight", "Domestic flight (Gimpo-Gimhae)", 55000, 1.0)],
    ("seoul", "jeju"): [("flight", "Domestic flight (Gimpo-Jeju)", 60000, 1.1), ("flight", "Low-cost carrier flight", 42000, 1.1)],
    ("busan", "jeju"): [("flight", "Domestic flight (Gimhae-Jeju)", 50000, 1.0), ("ferry", "Overnight ferry", 45000, 11.0)],
    ("seoul", "gyeongju"): [("train", "KTX to Singyeongju", 49300, 2.2), ("bus", "Express bus", 25000, 3.8)],
    ("busan", "gyeongju"): [("bus", "Intercity bus", 5200, 0.9), ("train", "ITX/Mugunghwa train", 7000, 1.0)],
    ("gyeongju", "jeju"): [("flight", "Flight via Pohang/Gimhae", 70000, 2.5)],
}

# Used for cities that are not in the catalog (live OpenStreetMap results).
GENERIC_COSTS = {"attraction": 8000, "museum": 6000, "restaurant_lunch": 12000, "restaurant_dinner": 22000,
                 "transit_per_day": 7000, "breakfast_normal": 9000, "breakfast_budget": 5000}
GENERIC_HOTELS = [("gen_gh", "Central guesthouse", 1, 40000), ("gen_budget", "Budget hotel", 2, 70000),
                  ("gen_mid", "Mid-range hotel", 3, 120000), ("gen_up", "Upscale hotel", 4, 250000)]


def find_city(name: str) -> str | None:
    """Map a free-text destination to a catalog key."""
    if not name:
        return None
    n = name.strip().lower()
    for key, city in CITIES.items():
        if n == key or n == city["name"].lower() or n in city.get("aliases", []):
            return key
    for key, city in CITIES.items():
        if key in n or any(a in n for a in city.get("aliases", [])):
            return key
    return None


def attractions(city_key: str) -> list[dict]:
    city = CITIES[city_key]
    return [{"id": a[0], "name": a[1], "kind": A, "tags": a[2], "indoor": a[3], "cost": a[4], "hours": a[5],
             "lat": a[6], "lon": a[7], "area": a[8], "popularity": a[9], "description": a[10],
             "source": "catalog"} for a in city["attractions"]]


def restaurants(city_key: str) -> list[dict]:
    city = CITIES[city_key]
    return [{"id": r[0], "name": r[1], "kind": R, "tags": r[2], "cost": r[3], "meal": r[4], "indoor": True,
             "hours": 1.0, "lat": r[5], "lon": r[6], "area": r[7], "popularity": r[8], "source": "catalog"}
            for r in city["restaurants"]]

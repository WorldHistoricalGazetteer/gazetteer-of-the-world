# Candidate gazetteers for future WHG ingestion

This list was compiled by **Palak Vashist** as a companion bibliography to the
*Gazetteer of the World* (GOTW) demonstrator. It identifies historical gazetteers
that could be processed by the same OCR + LLM pipeline used here, with a deliberate
focus on regions, periods, and languages that are under-represented in WHG today —
South Asia in particular, plus a global comparator set.

Source: [Candidate Bibliography spreadsheet (Google Drive)](https://docs.google.com/spreadsheets/d/1K8bNtpIdZofD0y7lRUbriymA7OKP-qy8/edit?usp=sharing&ouid=107770198717442836153&rtpof=true&sd=true).
The data below is a snapshot of that workbook; the spreadsheet remains the
working copy.

> **Caution.** Many entries are colonial, military, or ethnographic in
> framing. They should be ingested as **attestations** — a record that a name
> or place was used in a particular source at a particular date — not as
> authoritative statements of fact. Rows tagged *search lead* still need
> title-page, copyright, PDF/full-view, and repository verification before being
> treated as confirmed candidates.

---

## Selection rubric

| Criterion | What it means | Score guidance |
|---|---|---|
| **Regionally focused** | District / province / state / island / valley, not the whole world. | High = district/province/local; Medium = national; Low = global. |
| **Authoritative provenance** | Government department, recognised compiler, scholarly project, or local body. | High = official/scholarly; Medium = editorial; Low = unclear. |
| **Out of copyright** | Likely public domain (typically pre‑1929 for US status; verify by jurisdiction). | High = pre‑1929; Medium = ambiguous/reprint; Low = modern copyrighted. |
| **PDF or scan availability** | Downloadable scans on Internet Archive, HathiTrust, LOC, national libraries, etc. | High = PDF/text downloadable; Medium = catalog/full view only; Low = not digitised. |
| **Data richness** | Names, containment, coordinates, population, feature types, routes, rivers, maps, tables. | High = many structured place facts; Medium = narrative with some entries; Low = little place data. |
| **WHG gap value** | Fills regions / periods / languages / feature types under-represented in WHG. | High = South Asia / NE India / Frontier / Africa / SE Asia; Medium = comparator; Low = already well covered. |
| **Workflow fit** | Likely parseable by the OCR/LLM pipeline with modest prompt changes. | High = consistent alphabetical entries; Medium = mixed narrative/table; Low = complex script/layout/manual. |
| **Provenance / ethics risk** | Colonial, racial, ethnographic, military, or politically sensitive framing. | Flag all colonial/military sources with caution notes. |

---

## Search strategy

| Path | What was searched | Useful terms | Notes |
|---|---|---|---|
| **Internet Archive** | Direct PDFs/scans of public-domain gazetteers, often via Digital Library of India and Google Books mirrors. | `gazetteer` + district/province; *"Bombay Presidency"*, *"Burma Gazetteer"*, *"Bengal District Gazetteers"*, *"Madras District Gazetteers"*. | Best immediate source for downloadable scans and OCR text. Metadata can be messy — title-page inspection often needed. |
| **HathiTrust** | Catalog records and full-view scans for older gazetteers. | *"Gazetteer of the Bombay Presidency"*, *"Bengal district gazetteers"*, *"Mysore Gazetteer"*, *"Assam District Gazetteers"*. | Good for bibliographic control; full-view/download availability varies by institution and location. |
| **Library of Congress** | Public-domain PDFs, especially US/UK/world gazetteers and some South Asia material. | `site:loc.gov/resource gazetteer PDF`; *"Lippincott gazetteer"*, *"New England gazetteer"*, *"Gazetteer of the State of New York"*. | Excellent for LOC-hosted PDFs; weaker on South Asian district series than IA/DLI. |
| **NDLI / Indian Culture / IGNCA** | Indian public repositories and government scans; provenance for DLI items. | *"district gazetteer"*, *"Bombay Presidency"*, *"Central Provinces District Gazetteers"*, *"Gazetteer of India"*. | Use to verify Indian institutional provenance and locate volumes that are hard to find via IA. |
| **South Asia Open Archives** | Open South Asian primary-source collections, including provincial/district gazetteers. | `gazetteers`, `provincial series`, `princely states`, `Ceylon`, `Nepal`. | Curated discovery layer; then record item-level PDFs. |
| **State government / library portals** | Modern hosted copies of older district/state gazetteers. | *"district gazetteer" site:.nic.in*; Raj Bhavan e-library gazetteer; state archive gazetteer. | Useful but rights can be mixed, especially post-independence gazetteers; prioritise pre‑1929 for now. |
| **Multilingual searches** | Non-English gazetteer equivalents outside British colonial sources. | *"diccionario geográfico"*, *"nomenclátor"*, *"dictionnaire géographique"*, *"Kamus al-Alam"*, *"salname"*. | Important for Latin America, the Philippines, Ottoman / Middle East, and other non-English archives. |
| **Global companion searches** | Used to expand beyond South Asia. | *"East Africa gazetteer"*, *"Egypt gazetteer"*, *"Palestine handbook gazetteer"*, *"Philippine Islands gazetteer"*, *"Dutch East Indies gazetteer"*, *"Queensland gazetteer"*, *"Cuba gazetteer"*, *"Hungary gazetteer"*, *"Ireland gazetteer"*. | Rows added to the Candidate Bibliography under the same schema. |

---

## Candidates

Each candidate is listed in compact form:

> **[Title](source-url)** — *Compiler*, *Year(s)*. *Region covered*. **Status:** scan/PDF availability. **Next:** suggested action. *Tags*

### South Asia — India

- **[Gazetteer of the Bombay Presidency](https://archive.org/details/in.ernet.dli.2015.280089)** — Bombay Presidency / James M. Campbell and others, 1877–1896. Vol. I Part II / multiple district volumes; western India, Gujarat, Konkan, Deccan, Sind-related volumes. **Status:** PDF/scans on Internet Archive; HathiTrust catalog record exists. **Next:** inspect top 3 volumes (Bombay city/island, Kaira–Panch Mahals, Kathiawar/Konkan); test entry extraction and containment parsing. *South Asia; Bombay; colonial; district gazetteer.*
- **[Imperial Gazetteer of India, Provincial Series — Bombay Presidency Vols. I–II](https://library.bjp.org/jspui/handle/123456789/1927)** — compiled under Government of India / Sir William S. Meyer et al., 1908–1909. **Status:** PDF available; DSAL searchable online edition. **Next:** use as benchmark/control dataset against older Bombay Presidency volumes. *South Asia; Bombay; imperial; provincial.*
- **[Imperial Gazetteer of India, 26-volume new edition](https://dsal.uchicago.edu/reference/gazetteer/)** — Meyer, Burn, Cotton, Risley and others, 1908–1931 (core vols. 1908–1909). All-India, Burma, Baluchistan, princely states. **Status:** searchable web edition; many PDFs via IA/Burmalibrary/e-libraries. **Next:** use selectively; prioritise volumes/regions least represented in WHG. *South Asia; imperial; all-India; authority.*
- **[Bengal District Gazetteers](https://catalog.hathitrust.org/Record/006214589)** — Bengal Government / district officers, 1905–1920s. 24-Parganas, Saran, Manbhum, Santal Parganas, etc. **Status:** HathiTrust catalog; many volumes mirrored on IA/DLI. **Next:** identify 5 high-value district volumes from IA/DLI and mark exact PDFs. *South Asia; Bengal; district gazetteer.*
- **[Madras District Gazetteers](https://archive.org/details/dli.ministry.08435)** — Madras Government / district gazetteer compilers, 1906–1915+. Malabar and Anjengo; Cuddapah; etc. **Status:** PDF/scans on IA. **Next:** choose Malabar, Cuddapah, Tanjore/Trichinopoly if available; test taluk-level containment extraction. *South Asia; Madras; district gazetteer.*
- **[Punjab District Gazetteers](https://archive.org/details/dli.ministry.08591)** — Punjab Government / district officers, 1883–1920s. Kangra, Jullundur, Hoshiarpur, Hazara, etc. **Status:** PDF/scans available for many volumes. **Next:** prioritise Hazara, Kangra, Hoshiarpur, Jullundur, Delhi/Ambala older editions. *South Asia; Punjab; NW India; district gazetteer.*
- **[Gazetteer of the Hazara District (1907)](https://ia801404.us.archive.org/34/items/in.ernet.dli.2015.29896/2015.29896.Gazetteer-Of-The-Hazara-District-1907_text.pdf)** — H. D. Watson, Settlement Officer, 1907. Hazara District, NW Frontier / Punjab region. **Status:** text/PDF via IA. **Next:** add to short pilot list for South Asia beyond present-day India. *South Asia; Pakistan; Hazara; frontier.*
- **[Gazetteer of the Delhi District (1883)](https://archive.org/details/in.ernet.dli.2015.55921)** — Punjab Government / district compilation, 1883. **Status:** PDF/scans available. **Next:** use as a compact early pilot volume. *South Asia; Delhi; district gazetteer.*
- **[District Gazetteers of the United Provinces of Agra and Oudh](https://archive.org/details/in.ernet.dli.2015.16071)** — H. R. Nevill and others, 1903–1922. Agra, Allahabad, Meerut, Saharanpur, etc. **Status:** PDF/scans available for many district volumes. **Next:** select Agra, Meerut, Allahabad, Saharanpur as a representative pilot set. *South Asia; Uttar Pradesh; district gazetteer.*
- **[Central Provinces District Gazetteers](https://archive.org/details/centralprovinces10cent)** — Central Provinces Government, 1905–1920s. Bilaspur, Raipur, Saugar/Sagar, Nimar, Chhattisgarh Feudatory States; CP, Berar, Chhattisgarh, MP region. **Status:** PDF/scans available for many volumes. **Next:** prioritise descriptive district volumes before statistical table-only volumes. *South Asia; Central Provinces; district gazetteer.*
- **[Bihar and Orissa District Gazetteers](https://archive.org/details/dli.ministry.07450)** — Bihar and Orissa Government, 1907–1920s. Ranchi, Patna, Puri, Cuttack, etc.; Bihar, Jharkhand, Odisha. **Status:** PDF/scans available for examples. **Next:** find complete series list; prioritise Ranchi, Singhbhum, Cuttack/Puri. *South Asia; Bihar; Orissa; Jharkhand.*
- **[The Himalayan Gazetteer](https://ia800502.us.archive.org/10/items/in.ernet.dli.2015.129014/2015.129014.The-Himalayan-Gazetteer-Voliii-Part-Ii_text.pdf)** — E. T. Atkinson, 1882–1886. Vols. II–III parts; NW Provinces Himalayan districts, Kumaon, Garhwal; India / Nepal borderlands. **Status:** text/PDF via IA. **Next:** evaluate whether sections are entry-like enough or need a customised prompt/template. *South Asia; Himalaya; physical geography.*
- **[The East India Gazetteer](https://ia801409.us.archive.org/8/items/in.ernet.dli.2015.61933/2015.61933.The-East-India-Gazetteer-Voli_text.pdf)** — Walter Hamilton, 1815/1828 editions. Vols. I–II; India and surrounding regions. **Status:** text/PDF via IA. **Next:** treat as a historical attestation layer, not a primary district containment source. *South Asia; early 19th c.; colonial.*
- **[Rajasthan / Rajputana Gazetteer materials](https://dsal.uchicago.edu/reference/gazetteer/)** — Government of India / Rajputana Agency, 1908–1910s. Imperial Gazetteer Provincial Series: Rajputana; Ajmer-Merwara; princely states. **Status:** searchable online; PDFs via IA/e-libraries. **Next:** identify Rajputana-specific volumes and compare with Bombay/Punjab pilots. *South Asia; Rajputana; princely states.*
- **[Mysore and Coorg Gazetteer](https://archive.org/search?query=%22Mysore+Gazetteer%22)** — B. L. Rice and Mysore administration, 1870s–1890s. Mysore State / Coorg. **Status:** multiple scans likely available; verify best edition. **Next:** search IA/HathiTrust for the edition with the most entry-like structure. *South Asia; Mysore; Coorg; princely state.*
- **[Travancore State Manual](https://archive.org/search?query=%22Travancore+State+Manual%22)** — V. Nagam Aiya, 1906. Gazetteer-like sources for Travancore. **Status:** scans likely available; exact PDF to verify. **Next:** include as secondary candidate after stricter gazetteers. *South Asia; Travancore; princely state.*
- **[Hyderabad State / Deccan gazetteer materials](https://archive.org/search?query=%22Hyderabad+State+Gazetteer%22)** — Hyderabad / Deccan compilers, late 19th–early 20th c. **Status:** needs exact source verification. **Next:** search national libraries and HathiTrust for the authoritative volume. *South Asia; Hyderabad; Deccan; princely state.*
- **[Assam District Gazetteers](https://archive.org/search?query=%22Assam+District+Gazetteers%22)** — Assam Government / district compilers, 1905–1920s. Goalpara, Sylhet, Cachar, etc.; Assam, hill districts, Sylhet/Cachar (India / Bangladesh). **Status:** likely multiple scans; exact PDFs to verify. **Next:** build sub-bibliography from IA/HathiTrust/SAOA. *South Asia; Assam; Northeast India.*
- **[Kashmir / Jammu and Kashmir Gazetteer](https://archive.org/search?query=%22Gazetteer+of+Kashmir%22)** — Charles Ellison Bates and others, 1870s–1890s. Kashmir, Jammu, Ladakh; India / Pakistan / China borderlands. **Status:** likely scans available; exact title to verify. **Next:** verify best edition and rights; treat as a high-value but sensitive pilot. *South Asia; Kashmir; Ladakh; borderlands.*

### South Asia — Sri Lanka, Nepal, Afghanistan

- **[The Ceylon Gazetteer](https://ia801503.us.archive.org/28/items/in.ernet.dli.2015.94788/2015.94788.The-Ceylon-Gazetteer_text.pdf)** — Simon Casie Chitty / Ceylon compiler tradition, 1834. Ceylon / Sri Lanka. **Status:** text/PDF via IA. **Next:** high-priority non-India South Asia pilot. *South Asia; Sri Lanka; Ceylon; island.*
- **[Nepal gazetteer-like official/missionary sources](https://archive.org/search?query=%22Nepal%22+gazetteer)** — various, 19th–early 20th c. Nepal and adjoining Himalayan regions. **Status:** needs exact PDF identification. **Next:** targeted national-library and HathiTrust search. *South Asia; Nepal; Himalaya.*
- **[Gazetteer of Countries Adjacent to India on the North-West](https://archive.org/search?query=%22Gazetteer+of+Countries+Adjacent+to+India+on+the+North-West%22)** — Edward Thornton / East India Company context, 1844. NW India, Afghanistan, Baluchistan, Central Asian approaches. **Status:** likely scans available; exact item to verify. **Next:** find the best complete scan and confirm volume structure. *South Asia; Afghanistan; frontier; early colonial.*
- **[Afghanistan gazetteer / India Office frontier gazetteers](https://archive.org/search?query=%22Gazetteer+of+Afghanistan%22)** — India Office / General Staff / frontier administration, late 19th–early 20th c. Gazetteers of Afghanistan / Baluchistan / North-West Frontier. **Status:** scans likely, but permissions vary by edition. **Next:** find a public-domain edition and mark provenance prominently. *Afghanistan; frontier; military gazetteer.*

### Southeast Asia — Burma

- **[British Burma Gazetteer](https://ia803104.us.archive.org/1/items/in.ernet.dli.2015.531212/2015.531212.british-burma_text.pdf)** — British Burma administration, 1880. Vols. I–II; Lower/British Burma. **Status:** text/PDF via IA. **Next:** include as a South/Southeast Asia bridge candidate. *Southeast Asia; Burma; British India.*
- **[Gazetteer of Upper Burma and the Shan States](https://archive.org/stream/gazetteerupperb04hardgoog/gazetteerupperb04hardgoog_djvu.txt)** — J. George Scott and J. P. Hardiman, 1900–1901. Multi-volume; Upper Burma and Shan States. **Status:** scans/text via IA. **Next:** test one volume for extraction/reconciliation before tackling the whole series. *Southeast Asia; Burma; Shan States.*
- **[Burma Gazetteer: Akyab District](https://ia600600.us.archive.org/19/items/in.ernet.dli.2015.210387/2015.210387.Burma-Gazetteer_text.pdf)** — Burma Gazetteer series, early 20th c. Vol. A; Akyab/Arakan district. **Status:** text/PDF via IA. **Next:** inspect title page and table of contents; mark exact publication year. *Southeast Asia; Arakan; Burma district.*
- **[Burma Gazetteer: Toungoo District](https://ia601504.us.archive.org/8/items/in.ernet.dli.2015.206898/2015.206898.Burma-Gazetteer_text.pdf)** — Burma Gazetteer series, early 20th c. Toungoo District. **Status:** text/PDF via IA. **Next:** add to Burma sub-list; verify metadata. *Southeast Asia; Burma; district gazetteer.*

### Southeast Asia — Philippines and Dutch East Indies

- **[Philippines / Spanish colonial gazetteer candidates](https://archive.org/search?query=%22Diccionario+geografico%22+Filipinas)** — Spanish colonial compilers, 19th c. Diccionario geográfico/estadístico of the Philippines. **Status:** likely scans available; verification needed. **Next:** add to multilingual follow-up list; search BNE and IA. *Southeast Asia; Philippines; Spanish; multilingual.*
- **[A Pronouncing Gazetteer and Geographical Dictionary of the Philippine Islands](https://archive.org/details/pronouncinggazet00unitrich)** — U.S. Bureau of Insular Affairs; De B. Randolph Keim, 1902. Philippine Islands. **Status:** scans/downloads available. **Next:** prioritise for pilot sample after South Asia. *Philippines; global comparator; colonial; official/government.*
- **[Pronouncing Gazetteer of the Philippine Islands — direct PDF](https://archive.org/download/pronouncinggazet00unit/pronouncinggazet00unit.pdf)** — U.S. Bureau of Insular Affairs, 1902. **Status:** PDF available. **Next:** download and run sample extraction. *Philippines; global comparator.*
- **[Dutch East Indies / Java / Netherlands Indies gazetteer candidates](https://archive.org/search?query=Nederlandsch-Indie+gazetteer)** — Dutch colonial / statistical offices likely, pre‑1929 target. Java, Sumatra, Netherlands Indies. **Status:** search lead. **Next:** search Dutch terms — *aardrijkskundig woordenboek*, *plaatsnamen*, *Nederlandsch-Indië*. *Indonesia / Dutch East Indies; search lead; global comparator; colonial.*
- **[Java / Bali / Sumatra regional gazetteer leads (Dutch)](https://www.delpher.nl/)** — Dutch compilers, pre‑1929 target. **Status:** search lead. **Next:** use Dutch keywords and national-library catalogues. *Indonesia / Dutch East Indies; search lead; global comparator.*

### Middle East

- **[Gazetteer of the Persian Gulf, Oman and Central Arabia](https://archive.org/search?query=%22Gazetteer+of+the+Persian+Gulf%2C+Oman+and+Central+Arabia%22)** — J. G. Lorimer, 1908–1915. Persian Gulf, Oman, Central Arabia; Gulf states / Saudi Arabia / Iraq / Iran. **Status:** digital copies exist via Qatar Digital Library, IA, HathiTrust; PDF/download terms vary. **Next:** add as a blue-ribbon candidate but not first pipeline test. *Middle East; Gulf; Indian Ocean; colonial.*
- **[Ottoman / Middle East salname or geographical dictionary candidates](https://archive.org/search?query=%22Kamus+al-Alam%22+gazetteer)** — Ottoman compilers, late 19th c. Vilayet *salnames* / *Kamus al-A'lam* type sources; Ottoman provinces (Middle East / Balkans). **Status:** needs specialist search and language capability. **Next:** do not include in first pilot; mark as a long-term expert-source area. *Middle East; Ottoman; non-Latin script.*
- **[A Gazetteer of Egypt / Egypt topographical or government gazetteer leads](https://catalog.hathitrust.org/Search/Home?lookfor=Egypt+gazetteer&type=all)** — Survey of Egypt / government compilers likely, pre‑1929 target. Egypt: towns, Nile districts, oases, canals, ancient/modern sites. **Status:** search lead. **Next:** search exact variants in HathiTrust and LOC. *Egypt / Northeast Africa; search lead; global comparator; colonial; official/government.*
- **[The Handbook of Palestine](https://archive.org/download/handbookofpalest00luke/handbookofpalest00luke.pdf)** — Government of Palestine / Herbert Samuel authority context, 1922/1920s. Palestine: districts, towns, roads, population, administrative descriptions. **Status:** PDF available. **Next:** check table of contents/index for extractable place-entry sections. *Syria / Palestine / Levant; global comparator; colonial; official/government.*
- **[Gazetteer of Palestine / Syria official or survey gazetteers](https://archive.org/search?query=gazetteer+Palestine+Syria)** — Palestine Exploration Fund / government / survey compilers likely, pre‑1929 target. Palestine/Syria settlements, wadis, historical names. **Status:** search lead. **Next:** search for *"gazetteer Palestine Syria"* and verify full view. *Syria / Palestine / Levant; search lead; global comparator; colonial; official/government.*

### Africa

- **[South African local/geographical gazetteer candidates — Manual of South African Geography](https://archive.org/download/manualofsouthafr00hhal/manualofsouthafr00hhal.pdf)** — Henry Hall, 1866. Cape Colony / South Africa. **Status:** IA PDF available. **Next:** search further for true Cape Colony / Natal gazetteers or official blue-book indices. *Africa; South Africa; candidate search.*
- **[Prehistoric Rhodesia — appendix gazetteer](https://archive.org/download/prehistoricrhode00hall/prehistoricrhode00hall.pdf)** — R. N. Hall and related, 1909. Gazetteer of S.E. Africa, 915–1760 AD (appendix); Rhodesia / Southeast Africa. **Status:** IA PDF available. **Next:** inspect appendix only; assess as a specialised source. *Africa; historical; appendix gazetteer.*
- **[Prehistoric Rhodesia, with a Gazetteer of Medieval South-East Africa](https://archive.org/details/prehistoricrhode00hall)** — R. N. Hall, 1909. Monomotapa, Manica, Sofala, Mozambique / southeast Africa. **Status:** scans available. **Next:** inspect gazetteer section and test extraction on a sample. *East Africa / Southeast Africa; global comparator; colonial.*
- **[Gazetteer-style colonial handbook / official East Africa volumes](https://archive.org/search?query=East+Africa+gazetteer)** — British colonial offices / local administrations, pre‑1929 target. Kenya, Uganda, Tanganyika, Zanzibar, Mozambique region. **Status:** search lead. **Next:** search IA / HathiTrust with *Kenya Uganda Tanganyika Zanzibar gazetteer handbook official*. *East Africa; search lead; global comparator; colonial; official/government.*

### Oceania

- **[A Geographical Dictionary or Gazetteer of the Australian Colonies](https://archive.org/download/geographicaldict00wellrich/geographicaldict00wellrich.pdf)** — William Henry Wells, 1848. Australian colonies, especially NSW and colonial settlements. **Status:** PDF available. **Next:** add to high-priority comparator list. *Australia; global comparator; colonial.*
- **[Bailliere's Queensland Gazetteer and Road Guide](https://archive.org/download/baillieresqueens00whit/baillieresqueens00whit.pdf)** — Robert Percy Whitworth / Bailliere, 1876. Queensland. **Status:** PDF available. **Next:** inspect entry consistency and road-guide sections. *Australia; global comparator.*
- **[New Zealand gazetteer / Hocken or colonial gazetteer leads](https://natlib.govt.nz/search?search%5Btext%5D=New+Zealand+gazetteer)** — colonial / NZ compilers, pre‑1929 target. NZ towns, counties, provinces. **Status:** search lead. **Next:** search National Library NZ and IA. *New Zealand; search lead; global comparator; colonial; official/government.*

### Latin America & Caribbean

- **[A Gazetteer of Cuba](https://www.loc.gov/resource/gdcmassbookdig.gazetteerofcuba00gann/?st=pdf)** — Henry Gannett, 1902. Cuba. **Status:** LOC PDF/digital item available. **Next:** prioritise for Latin America / Caribbean section. *Latin America / Caribbean; global comparator; official/government.*
- **[Diccionario geográfico, estadístico, histórico de la isla de Cuba](https://archive.org/details/McGillLibrary-104423-132)** — Jacobo de la Pezuela, 1863. Cuba. **Status:** scans available (McGill digitised copy). **Next:** add as a high-priority multilingual candidate. *Latin America / Caribbean; global comparator; colonial.*
- **[Mexican geographical dictionary / *Diccionario geográfico* leads](https://catalog.hathitrust.org/Search/Home?lookfor=Diccionario+geografico+Mexico&type=all)** — Mexican national / state compilers, pre‑1929 target. Mexico: states, towns, municipalities. **Status:** search lead. **Next:** search Spanish terms in national-library catalogs. *Latin America / Mexico; search lead; global comparator.*
- **[Brazil geographical dictionary / Diccionario candidates](https://bndigital.bn.gov.br/)** — Brazilian / Portuguese compilers, pre‑1929 target. Brazilian provinces/states, towns, rivers. **Status:** search lead. **Next:** search Portuguese terms — *diccionario geographico Brazil gazetteer*. *Latin America / South America; search lead; global comparator.*
- **[Latin American regional gazetteer candidates](https://archive.org/search?query=%28gazetteer+OR+diccionario+geografico%29+Mexico+Peru+Chile+Brazil)** — various, 19th c. Search target: national / statistical / geographical dictionaries of Mexico, Peru, Chile, Brazil. **Status:** needs targeted multilingual search. **Next:** *diccionario geográfico*, *diccionario estadístico*, *nomenclátor*. *Latin America; multilingual; future search.*
- **[Ornithological Gazetteer of Brazil](https://ia800205.us.archive.org/23/items/ornithologicalga01payn/ornithologicalga01payn.pdf)** — Raymond A. Paynter Jr. and Melvin A. Traylor Jr., 1991. Brazil localities in ornithological records. **Status:** PDF available but modern. **Next:** do not include in priority list; note as a modern comparator only. *Latin America / Brazil; global comparator.*

### Eastern Europe

- **[Magyarország helységnévtára](https://archive.org/details/magyarorszghel01dvor)** — János Dvorzsák, 1893. Kingdom of Hungary / historical Hungary. **Status:** scans available. **Next:** prioritise as a non-English Eastern European pilot. *Eastern Europe / Hungary; global comparator.*
- **[Słownik geograficzny Królestwa Polskiego / Polish geographical dictionary leads](https://catalog.hathitrust.org/Search/Home?lookfor=Slownik+geograficzny+Krolestwa+Polskiego&type=all)** — Filip Sulimierski et al., 1880–1902. Polish lands and neighbouring Slavic / Eastern European regions. **Status:** search lead. **Next:** find stable scans/PDF and test one volume. *Eastern Europe / Poland; search lead; global comparator.*
- **[Balkan / Turkey-in-Europe gazetteer leads](https://archive.org/search?query=Turkey+in+Europe+gazetteer)** — British / European compilers, pre‑1929 target. Balkans, Ottoman Europe, provinces/towns. **Status:** search lead. **Next:** search *"Turkey in Europe gazetteer"*, *"Balkan gazetteer"*. *Eastern Europe / Balkans; search lead; global comparator.*

### British Isles (comparators)

- **[Index Villaris](https://archive.org/download/bookofbritishtop00andeuoft/bookofbritishtop00andeuoft.pdf)** — John Adams, 1680. England and Wales. **Status:** bibliographic references; exact scan of Index Villaris to locate/confirm. **Next:** locate full scan and compare to existing WHG Index Villaris ingestion. *Britain; parish; early modern; model source.*
- **[England's Gazetteer / New Index Villaris](https://archive.org/stream/englandsgazettee01whatiala/englandsgazettee01whatiala_djvu.txt)** — Stephen Whatley / related compilers, 1751. England and Wales, multi-volume. **Status:** full text/scans via IA. **Next:** use as a methodological comparator, not the highest gap priority. *Britain; village; parish; early modern.*
- **[History, Gazetteer, and Directory of Suffolk](https://www.loc.gov/resource/gdcmassbookdig.historygazetteer00whit_0/?st=pdf)** — William White, 1855. County of Suffolk. **Status:** LOC PDF available. **Next:** keep as a reference candidate for the blog's "local knowledge" framing. *Britain; county; directory.*
- **[The Parliamentary Gazetteer of Ireland](https://archive.org/details/parliamentaryga00unkngoog)** — A. Fullarton & Co., 1846. Ireland. **Status:** PDF and text downloads available. **Next:** prioritise as a UK / Ireland comparator. *UK / Ireland; global comparator; colonial.*
- **[The Parliamentary Gazetteer of Ireland — adapted edition](https://archive.org/details/bub_gb_RRRBxFTucEsC)** — A. Fullarton & Co., 1846. Adapted to poor-law / franchise / municipal arrangements. **Status:** digital item; public-domain mark. **Next:** verify volume completeness and scan quality. *UK / Ireland; global comparator.*
- **[Cassell's Gazetteer of Great Britain and Ireland](https://archive.org/details/cassellsgazette00unkngoog)** — Cassell and Co., 1899. **Status:** scans available. **Next:** keep as a comparator, not a top gap-filling priority. *UK / Ireland; global comparator; colonial.*
- **[The Survey Gazetteer of the British Isles](https://archive.org/details/surveygazetteero00bartuoft)** — J. G. Bartholomew, 1904. **Status:** scans available. **Next:** use for pipeline benchmarking only. *UK / Ireland; global comparator; official/government.*
- **[The Gazetteer of Scotland](https://archive.org/download/gazetteerofscov21838cham/gazetteerofscov21838cham.pdf)** — Chambers / Scottish compilers, 1830s/1838. Scotland. **Status:** PDF available. **Next:** use as a technical comparator for historical containment polygons. *UK / Scotland; global comparator.*
- **[The National Gazetteer: A Topographical Dictionary of the British Islands](https://archive.org/details/nationalgazettee03londuoft)** — Virtue & Co., 1868. British Isles. **Status:** scans available. **Next:** keep as a B-priority comparator. *UK / British Isles; global comparator.*

### North America (comparators)

- **[New England Gazetteer](https://www.loc.gov/resource/gdcmassbookdig.newenglandgazett00haywhawj/?st=pdf)** — John Hayward, 1839. New England states. **Status:** LOC PDF available. **Next:** use as a LOC example and methodology comparator. *North America; New England; LOC.*
- **[Gazetteer of the State of New-Hampshire](https://www.loc.gov/resource/gdcmassbookdig.gazetteerofstate00farm/?st=pdf)** — John Farmer and Jacob B. Moore, 1823. New Hampshire. **Status:** LOC PDF available. **Next:** use as an example of a non-South-Asia regional public-domain gazetteer. *North America; US; state gazetteer.*
- **[Gazetteer of the State of New York](https://www.loc.gov/resource/gdcmassbookdig.gazetteerofstate05fren/?st=brief)** — J. H. French, 1860. New York State, historical/statistical. **Status:** LOC PDF/text available. **Next:** keep in secondary non-South-Asia list. *North America; New York; LOC.*
- **[Gazetteer of the State of New York — direct PDF](https://tile.loc.gov/storage-services/public/gdcmassbookdig/gazetteerofstate04fren/gazetteerofstate04fren_bw.pdf)** — J. H. French, 1860. **Status:** PDF available. **Next:** use as an example of the LOC search route, not a core gap-list candidate. *North America comparator; global comparator; official/government.*
- **[Gazetteer of Hampshire County, Massachusetts, 1654–1887](https://www.loc.gov/resource/gdcmassbookdig.gazetteerofhamps00gayw/?sp=462)** — W. B. Gay, 1887. **Status:** LOC PDF available. **Next:** use as a comparator, not a WHG priority. *North America; Massachusetts; county gazetteer.*
- **[Gazetteer of the State of Michigan](https://tile.loc.gov/storage-services/service/gdc/lhbum/18627/18627.pdf)** — John T. Blois / state gazetteer tradition, 1838/1839. Michigan. **Status:** PDF available. **Next:** keep only in the comparator sheet if needed. *North America comparator; global comparator.*
- **[Complete Descriptive and Statistical Gazetteer of the United States](https://www.loc.gov/resource/gdcmassbookdig.completedescript01hask/?st=list)** — Daniel Haskel and John Calvin Smith, 1844. United States. **Status:** LOC PDF/text available. **Next:** use as a negative / comparator case. *North America; national gazetteer.*
- **[A Complete Reference Gazetteer of the United States](https://www.loc.gov/resource/gdcmassbookdig.completereferenc01chap/?st=pdf)** — Chapman / US compilers, 19th c. United States. **Status:** LOC PDF available. **Next:** keep as an LOC example only. *North America; LOC; national.*

### Global

- **[Lippincott's Gazetteer of the World](https://www.loc.gov/resource/gdc.00335879582/?st=gallery)** — J. B. Lippincott, 1889. World. **Status:** LOC digital item / PDF available. **Next:** do not prioritise, but cite as an LOC global comparator. *Global; LOC; comparator.*

### Repositories worth searching

- **[National Digital Library of India / Indian Culture / IGNCA gazetteer holdings](https://www.ndl.gov.in/)** — Indian government and cultural repositories; mixed dates. Government / district gazetteers, ASI scans, state gazetteers. **Status:** repository / search portal; item-level PDFs vary. **Next:** use alongside Internet Archive to verify provenance and locate missing volumes. *Repository; India; discovery.*

---

## Summary

| Metric | Count / note |
|---|---|
| Original rows from earlier Candidate Bibliography (South Asia focus) | 46 |
| Global rows added using the same column schema | 28 |
| Total candidate rows in the workbook | 74 |
| When to use this list | When you want South Asia and global candidates in one consistent WHG bibliography schema. |
| Important caution | Rows tagged *search lead* still require title-page / PDF / copyright verification. |

The global additions sit alongside the original South Asia candidates under the
same schema. To separate them, filter the *Tags* column for `global comparator`,
or the *Region covered* / *Current country/area* columns by South Asia vs. wider
regions.

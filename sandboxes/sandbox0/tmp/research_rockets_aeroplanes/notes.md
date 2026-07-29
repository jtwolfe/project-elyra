# Rockets & aeroplanes — open reading notes

Started: 2026-07-29
Sources: Wikipedia primarily; Grokipedia reachable (direct /page/ slugs + web_search site:; on-site /search UI broken from this harness).

---

## Rockets

### Core definition (Wikipedia: Rocket)
- Vehicle using a **rocket engine** — accelerates by expelling exhaust at high speed (reaction/momentum conservation). Carries all propellant including oxidizer → works in vacuum; often *more* efficient in vacuum than atmosphere.
- Distinct from air-breathing **jets** (which need atmospheric O₂).
- Not the same as **launch vehicle** (a launch vehicle is typically a multistage rocket system configured to put payload into space).
- Control: fins/airfoils (atmosphere), gimballed thrust, exhaust deflection, RCS/aux engines, spin, momentum wheels, or pure ballistic arc.
- Multistage → can reach escape velocity / unlimited altitude in principle.

### Propellant families (chemical rockets dominate high thrust)
- Pressurized cold gas
- **Monopropellant** (e.g. hydrazine over catalyst)
- **Hypergolic** bipropellant (ignite on contact)
- Non-hypergolic liquids needing ignition — classic **RP-1 + LOX**
- **Solid** (fuel+oxidizer premixed)
- **Hybrid** (solid fuel + liquid/gaseous oxidizer)
- Energy density high → accidents severe

### History trail
- **13th-c. China (Song)**: gunpowder rockets; early multi-launcher; Mongol transmission → Middle East/Europe
- **Huolongjing** (mid-14th, Jiao Yu): military rockets; early multistage concept *Huo long chu shui* (fire-dragon issuing from water)
- Hasan al-Rammah (~1270–80): many gunpowder/rocket recipes
- Europe: Roger Bacon, Liber Ignium, Kyeser *Bellifortis*, Fontana
- Etymology: Italian *rocchetta* / *rocchetto* = little bobbin/spool (shape)
- **Mysorean iron-cased rockets** (Hyder Ali / Tipu Sultan, late 18th c. India) → inspired **Congreve rockets** (Britain, 1804); "rockets' red glare" = Congreves at Fort McHenry 1814
- Range leap ~100 → ~2000 yards with Mysore/Congreve line
- Early theory: William Moore 1813 dynamics; **William Leitch** 1861 spaceflight-by-rocket essay; **Tsiolkovsky** 1903 foundational theory
- WWI guided-rocket experiments (RFC / Archibald Low)
- 20th c.: Space Age enabler (satellites, human spaceflight, Moon)

### Uses
Fireworks, missiles/weapons, ejection seats, launch vehicles, human spaceflight, exploration.


### Physics — rocket equation (Wikipedia: Tsiolkovsky rocket equation)
- Ideal/classical rocket equation (also derived earlier by William Moore 1810/1813; independently Goddard 1912, Oberth ~1920):
  **Δv = v_e · ln(m0 / mf) = Isp · g0 · ln(m0 / mf)**
  - m0 = wet mass, mf = dry mass, v_e = effective exhaust velocity
- Wet mass grows **exponentially** with desired Δv: m0 = mf · exp(Δv/v_e)
- Propellant mass fraction vs payload: (m0−mf)/mf = exp(Δv/v_e) − 1
- Δv is integrated acceleration from the engine (ideal free space); real flight subtracts gravity & drag losses
- Assumes constant v_e (Tsiolkovsky hypothesis); variable v_e complicates closed form
- Tsiolkovsky honored for applying it to *spaceflight feasibility*, not sole derivation

### Specific impulse (Isp)
- Impulse (momentum change) per propellant mass — efficiency of making thrust from reaction mass
- Equivalent to **effective exhaust velocity**; often quoted in **seconds**: Isp[s] = v_e / g0
  - Same number in SI and imperial when using force/weight units — handy engineering convention
  - Physical reading: how long 1 kg of propellant can produce 1 kgf of thrust
- Thrust T = v_e · dm/dt (const v_e)
- **Not directly comparable** across engine classes:
  - Rockets: per reaction mass (fuel+oxidizer carried)
  - Airplanes/jets: usually per *fuel* burned (air is free reaction mass) → jet Isp numbers look huge vs rockets
  - Cars: ground reaction; different meaning again
- Nozzle converts thermal/pressure energy → directed momentum; ambient pressure matters → **sea-level Isp < vacuum Isp** for the same chemical engine
- Can factor Isp via characteristic velocity c* (chamber) × thrust coefficient CF (nozzle)
- Ion engines: high Isp, low thrust; different loss mechanisms (no classic De Laval nozzle story)

### Multistage rockets
- Only multistage systems have reached orbit from Earth; SSTO not demonstrated from Earth surface
- Drop empty tanks/engines → raise mass ratio of what remains; split total Δv across stages
- **Serial/tandem** vs **parallel** (strap-on boosters often "stage 0")
- Hot-staging: upper stage lights before full separation
- Stage optimization: lower stages high thrust / atmosphere-tuned nozzles; upper stages vacuum-optimized, higher Isp
- Typical liftoff T/W ~ **1.3–2.0**
- Cost of staging: complexity, failure modes at sep/ignition, carrying unlit upper engines early
- Every orbital launcher has used some form of staging

### Oberth effect (powered flyby / Oberth maneuver)
- Burn deep in a gravity well (near periapsis, high speed) → more gain in **mechanical energy** for the same Δv than burning slow/high
- KE ∝ v² so +Δv at high v adds more energy; work W = F·s done while moving fast deposits more energy into vehicle
- At high speed propellant carries KE too; exhausting it rearward drops propellant KE more usefully into vehicle KE
- Needs **high thrust / short burn** near periapsis → chemical rockets excel; ion drives must split burns over many periapsis passes
- Explains why upper stages can produce more vehicle KE than chemical energy in their propellant alone suggests
- Distinct from (but often combined in mission design with) gravity assists


### Rocket engines (Wikipedia: Rocket engine)
- Reaction engine: thrust by ejecting mass rearward (Newton 3). Usually hot combustion gas; also cold gas, NTR, ion, etc.
- Carry oxidizer → vacuum-capable; highest thrust among jet-family engines but **lowest Isp** (least propellant-efficient)
- Chemical classes: solid motors, liquid (pumped or pressure-fed), hybrid, monopropellant (hydrazine, H2O2 over catalyst)
- Liquid path: tanks → pumps/pressurization → injectors → chamber → **de Laval nozzle**
- Chamber: extreme T & P (no N2 diluent like air-breathers); L* = Vc/At characteristic length ~0.64–1.52 m typical
- Feed pressure must exceed chamber pressure (turbopumps or strong tank pressure)
- Cooling critical (regenerative, film, ablative, etc. — page covers mechanical/acoustic/safety issues)
- Thermal rockets heat inert propellant (electric or nuclear); NTR high Isp vs chemical but low thrust / political-environmental barriers for Earth use
- Note: page carried a Sept 2025 LLM-contamination warning — treat details cautiously; cross-check elsewhere

### De Laval (convergent–divergent) nozzle
- Pinch (throat) then diverge: convert thermal energy → directed kinetic energy; enable **supersonic** exhaust
- Gustaf de Laval 1888 (steam turbines); Goddard first applied to rockets; now standard for hot-gas rocket nozzles
- Subsonic: narrowing accelerates flow → **Mach 1 at throat** (choked) if pressure ratio high enough; diverge further → supersonic expansion
- Isentropic idealization; ideal gas assumptions in textbook analysis
- Exit pressure vs ambient: over/under-expansion; if exit pressure too low vs ambient, separation / unstable jet / side loads
- ve depends on chamber T, molecular weight M (light exhaust better), γ, pe/p ratio — **why LH2/LOX wins Isp** (low M)
- Ballpark ve: mono ~1.7–2.9 km/s; biprop ~2.9–4.5 km/s; solid ~2.1–3.2 km/s

### History extras
- Precursors: Archytas steam pigeon (~400 BC); Hero aeolipile — action-reaction demos, not true gunpowder rockets
- Gunpowder rocket dating debated (Song claims 969/1000 vs Needham: suitable propellant only later)
- Spread: Mongols; Middle East (Hasan al-Rammah); Korea hwacha; Europe Chioggia 1380
- Mysore iron-cased → British Congreve line
- **Robert H. Goddard** (1882–1945): first liquid-fueled flight **16 Mar 1926**; 34 flights 1926–41 to 2.6 km / 885 km/h; 1914 patents multi-stage & liquid fuel; 1919 *A Method of Reaching Extreme Altitudes*; gyro + steerable thrust control; little contemporary support, later recognized with Tsiolkovsky, Oberth, Esnault-Pelterie as founding fathers; NASA GSFC named for him

### Link trail still worth later
- V-2 / Aggregat, launch vehicle, orbital mechanics, Hohmann transfer, gravity assist, RP-1, LOX/LH2, ion thruster, Falcon/ reusable economics


---

## Aeroplanes / airplanes

### Core definition (Wikipedia: Airplane)
- **Fixed-wing** aircraft propelled by jet, propeller, or (rarely) rocket thrust.
- US/Canada: airplane; UK/Commonwealth: aeroplane (NACA standardized "airplane" in US 1916).
- Etymology: French *aéroplane* from Greek *aēr* + Latin *planus* / Greek *planos* — originally the *wing*, then whole craft (synecdoche).
- Uses: transport (4B+ pax/yr commercial; cargo tonne-km huge but <1% world cargo by mass-distance framing in article), military, research, recreation; crewed or UAV.
- Inventors credited: Wright brothers, 1903 (first sustained controlled powered heavier-than-air flight).

### Prehistory → first flights
- Myth/legend: Icarus; Vimana; Archytas steam bird again
- Early glider attempts: Abbas ibn Firnas (9th c.), Eilmer of Malmesbury (11th)
- Leonardo: bird flight studies; mass vs pressure center distinction
- **George Cayley (1799)**: modern airplane concept — **separate lift, propulsion, control**; models 1803; passenger glider 1853
- Le Bris horse-towed Albatros; Montgomery gliders; Maxim steam testbed (lift but uncontrollable)
- **Otto Lilienthal**: repeated documented glides; thin curved airfoils; 1891 often called start of human flight; Normalsegelapparat as early series "airplane"; major Wright influence
- Hargrave box kites; Ader Éole 1890 powered hop (~50 m, uncontrolled)
- **Wrights, 17 Dec 1903**, Kill Devil Hills near Kitty Hawk: Wright Flyer — first controlled sustained powered heavier-than-air flight
  - Breakthrough = **three-axis control** (not just a big engine); patent was about control system
  - Home wind tunnel → better wing/prop data; bicycle-shop culture of balance/practice
  - Flyer II (1904 circles); Flyer III (1905) first practical airplane
  - Mechanic Charles Taylor built first engine with them

### Jet age milestones (airplane article)
- First jet aircraft: **Heinkel He 178** (1939)
- First jet airliner: de Havilland Comet (1952)
- First widely successful jetliner: Boeing 707 (service 1958–2019)

### Aerodynamics (Wikipedia: Aerodynamics)
- Study of air motion especially around solids; fluid/gas dynamics applied to flight
- Four forces (Cayley): **lift, weight, thrust, drag**
- Theory lineage: Newton air resistance; **Bernoulli 1738** (p–v relation incompressible); Euler eqs; **Navier–Stokes** (+viscosity) — general but hard
- Wenham wind tunnel 1871; Renard power estimates; Lanchester / Kutta / Zhukovsky circulation→lift; **Prandtl** thin-airfoil, lifting-line, boundary layer
- Speed regimes: subsonic → **transonic** (von Kármán/Dryden term) → supersonic → hypersonic
- Compressibility, shocks, flutter; Mach number (Ernst Mach); Rankine–Hugoniot shocks
- Sound barrier broken 1947 **Bell X-1**
- Modern: CFD + tunnel + flight test; open math problems still around turbulence / N-S existence-uniqueness

### Lift (force)
- Component of aerodynamic force **perpendicular** to freestream; drag parallel
- Not always "up" — defined vs flow, so can be sideways (keel/sail) or downforce
- Distinct from buoyancy (aerostatic) and planing lift
- Airfoil: much more lift than drag vs flat plate
- Two incomplete popular stories that are both partly right:
  1. **Newton / flow turning**: wing pushes air down → air pushes wing up (3rd law). Upper surface does much of the turning. Incomplete without pressure field / how deep the flow is turned.
  2. **Bernoulli / pressure**: faster flow ↔ lower pressure on upper surface → net upward force. Incomplete alone without explaining *why* speeds differ and matching far-field momentum.
- Coandă-effect popular explanations are contested in aero literature when applied loosely to ordinary airfoil boundary layers
- Full picture needs pressure integration on surface **and** momentum change in the flow; circulation theories (Kutta–Joukowski) quantify 2D lift

### Jet engines (Wikipedia: Jet engine)
- Reaction engines dumping a fast jet of fluid; in practice "jet engine" = air-breathing IC: turbojet, turbofan, ramjet, pulsejet, scramjet (rockets often excluded from the colloquial term)
- Classic core: compressor + combustor + turbine + nozzle (**Brayton cycle**); turbine drives compressor
- **High-bypass turbofan** dominates modern airliners — better subsonic SFC than pure turbojets
- Ramjet/scramjet: use ram compression, no (or limited) mechanical compressor; need high speed already
- History drivers: propeller tip-speed / efficiency limits near Mach 1
- Parallel inventors: **Frank Whittle** (patent filed 1930, granted 1932; centrifugal focus) and **Hans von Ohain** (Heinkel; He 178 first jet flight 27 Aug 1939)
- WWII: Junkers **Jumo 004** axial compressor — Me 262, Ar 234
- Thrust growth example: ~22 kN Ghost era → ~510 kN GE90 class; IFSD rates collapsed → ETOPS twin-oceanic normals
- Propulsion contrast with rockets: jets **breathe oxidizer**; reaction mass mostly air → much higher effective Isp *per fuel* but need atmosphere and have intake/drag complexity

### Grokipedia attempt
- Site up (grokipedia.com); homepage search UI works
- `/page/Rocket` → 404; search for "Rocket" returned "Failed to search"
- Continue primarily on Wikipedia; retry Grokipedia later if useful

### Aircraft link trail still worth later
- Airfoil, Bernoulli principle, drag (physics), turbofan, propeller, flight control surfaces, stability and control, sound barrier, Concorde, wing configurations, helicopter (rotorcraft contrast)


### Turbofan (deep dive)
- Turbojet core + **ducted fan** driven by extra turbine stages; some inlet air **bypasses** the core
- **Bypass ratio** BPR = ṁ_bypass / ṁ_core
  - High-BPR: airliners (most thrust from large slow fan stream)
  - Low-BPR: fighters; often mix + afterburner
- Why more efficient at subsonic speeds: same thrust with **more mass, less Δv** of exhaust → better propulsive (Froude) efficiency; wake kinetic energy waste drops
- Restores something like piston+prop independence of thermal vs propulsive efficiency: fan pressure ratio sets specific thrust somewhat separately from core cycle
- Whittle already sketched the idea in 1936 patent language (propel more air slower)
- Trade: more intake ram drag from bigger stream tube, but net thrust and SFC still win for airliners
- Configurations: 2-spool common (LP blue / HP orange in schematics); fan on LP spool; geared turbofans add a gearbox so fan can spin slower than LP turbine optimum

### Airfoil / aerofoil
- Streamlined cross-section generating much more lift than drag
- AoA primary; **camber** allows lift at zero AoA; symmetric better for aerobatics/inverted
- Subsonic: rounded LE; supersonic: thinner, sharper LE, more angular; supercritical shapes manage transonic shocks
- Lift curve ~linear with AoA until **stall** (~15–18° typical example) when upper BL separates → lift drop, drag spike
- Kutta–Joukowski: L' = ρ∞ V∞ Γ (2D) links circulation to lift
- High-lift devices: flaps, slats change effective camber/area for takeoff/landing
- Laminar-flow sections push max thickness aft to keep laminar BL longer (drag ↓) but dirty bugs/roughness kill the benefit — gliders love them; high-speed airliners historically struggled until better manufacturing

### Flight controls (fixed-wing)
- Three axes (Wright insight, standardized cockpit layout popularized via Blériot / Esnault-Pelterie pattern):
  - **Roll** — ailerons (or wing warp historically) via stick/yoke left-right
  - **Pitch** — elevator via stick/yoke fore-aft
  - **Yaw** — rudder via pedals
- Throttle/thrust levers also "flight controls" in the broad sense (energy/speed)
- Secondary: trim tabs (reduce stick force), flaps/slats, spoilers, speed brakes, variable sweep
- Actuation evolution: pure mechanical cables/pushrods → hydro-mechanical → **fly-by-wire** (computers + actuators) with artificial feel / envelope protection
- Combined surfaces still map to same three axes: elevons, ruddervators, flaperons

### Rockets vs aeroplanes — contrast table (synthesis)
| | Aeroplane | Rocket |
|---|---|---|
| Lift | Aerodynamic (wings) for most of flight | Thrust > weight; little/no wing lift at launch (some boosters/glide stages later) |
| Oxidizer | From air | Carried |
| Best regime | Atmosphere, subsonic–low supersonic routinely | Vacuum OK; atmosphere is lossy |
| Efficiency metric | SFC / passenger-mile; high effective Isp per *fuel* | Isp on all propellant; staging essential to orbit |
| Control | Aero surfaces + thrust | Gimbal/TV C, fins in atmo, RCS in space |
| Failure physics | Stall, flutter, CFIT, engine-out glide possible | Staging sep, LOX/fuel handling, structural loads, no glide from vacuum |


### V-2 / Aggregat-4 (Wikipedia: V-2 rocket)
- World's first practical modern **ballistic missile** and suborbital LV; Nazi Germany WWII "Vergeltungswaffe 2"
- Peenemünde Army Research Center; key figure **Wernher von Braun** (influenced by Oberth; Army sponsorship via Dornberger)
- Specs (order of magnitude): ~12.5 t, 14 m tall, 1.65 m diameter; propellant ~3.8 t of 75% ethanol/25% water + ~4.9 t LOX; range ~320 km; max speed ~1.6 km/s; apogee ~88 km on long-range traj, ~206 km vertical
- Guidance: gyros + autopilot; pendulous integrating gyro accelerometer for engine cutoff on most production units; mobile Meillerwagen launch
- First artificial object through the later-defined 100 km Kármán line: vertical launch MW 18014, 20 Jun 1944
- Combat: from Sep 1944, >3000 launched (London, Antwerp, Liège…); ~9000 deaths from attacks; ~12,000 forced laborers/camp prisoners died in production (Mittelwerk) — strategic impact assessed as small vs cost; terror + unstoppable (supersonic impact, no warning)
- Tech package that mattered post-war: large LOX/alcohol engine, supersonic aero, gyro guidance, jet vanes/rudders in exhaust
- Allied scramble for tech (Paperclip etc.) seeded US/USSR missile and space programs


### Hohmann transfer orbit
- Two-impulse transfer between coplanar circular orbits via an **ellipse tangent** to both
- Burn 1 at periapsis of transfer: raise apoapsis to target radius
- Burn 2 at apoapsis: raise periapsis → circularize (or reverse to go down)
- Often **minimum Δv** among two-impulse transfers; slower than high-energy paths
- When r2 ≫ r1, **bi-elliptic** can beat Hohmann on Δv at cost of time
- Walter Hohmann 1925 *Die Erreichbarkeit der Himmelskörper*
- Interplanetary: needs planetary alignment → **launch windows** (Earth–Mars ~26 months); transfer time Earth–Mars ~9 months classically
- Type I (<180° true anomaly sweep) vs Type II (>180°); multi-rev Type III/IV
- Vis-viva: v² = μ(2/r − 1/a); Δv formulas from circular speed difference vs ellipse speeds at r1, r2
- Near planets, combine with **Oberth** (burn deep in gravity well) and sometimes low-energy/ITN paths vs pure Hohmann

### Orbital mechanics quick stack (synthesis)
- LEO circular ~7.8 km/s; reaching orbit is mostly **horizontal speed**, not altitude
- Gravity losses, drag losses, steering losses on ascent; staging drops dry mass (rocket equation)
- Once in space: coast on conics (two-body); maneuvers change a, e, i with Δv budget
- Plane changes are expensive (best at low v / apoapsis); combine with other burns when possible

---

## Session log
- 2026-07-29 ~13:20–13:30 UTC: open-ended browse pass for Jim
- Sources: English Wikipedia primary; Grokipedia homepage reachable but search failed / direct Rocket page 404
- Notes file grown through rocket fundamentals, engines, history, aircraft, aero, propulsion, V-2, Hohmann
- Browser session used throughout; ledger goal g_71edea050f74

---

## Grokipedia access probe (2026-07-29 ~13:33 UTC)

Follow-up after earlier `/page/Rocket` 404 and failed UI search. Jim shared Grok's guidance (xAI `web_search` + `allowed_domains=["grokipedia.com"]`; direct `/search?q=` and `/page/Topic_Name`; unofficial scrapers). This harness has plain `web_search` (no domain filter) + Playwright browse.

### What works from this harness

| Path | Result |
|------|--------|
| `web_search` query `site:grokipedia.com …` | **Works.** Returns real article titles, `/page/…` URLs, and snippets. Best discovery path here. |
| Direct `https://grokipedia.com/page/<Slug>` | **Works** when the article exists. Full article text via `browser_get_text` (a11y tree + body). |
| `https://grokipedia.com/` homepage | **Works** (200). |
| `https://grokipedia.com/search?q=rocket` | **HTTP 200 but UI fails:** page shows *"Failed to search. Please try again."* — no result list. Matches earlier broken on-site search experience. |
| xAI-style `web_search(allowed_domains=[…])` | **Not available** on this tool — Grok-stack only. |

### Slug notes
- Earlier `/page/Rocket` → **404**; same URL now → **200** with a full Rocket article (fact-checked by Grok). Treat 404s as possibly transient or slug-timing, not "site down."
- Confirmed 200 + readable body:
  - `/page/Rocket`
  - `/page/Tsiolkovsky_rocket_equation`
  - `/page/Rocket_Lab`
  - `/page/Airplane`
  - `/page/Rocket-powered_aircraft`
- Slug style is Wikipedia-like: spaces → `_`, parentheses kept (`Zuni_(rocket)`), hyphens in multiword titles (`Rocket-powered_aircraft`).
- Prefer **discover via `web_search site:grokipedia.com`**, then **goto the exact URL** from hits — do not guess bare titles blindly.

### Practical recipe (this harness)
1. `web_search` with `site:grokipedia.com <topic>`
2. `browser_goto` a hit URL under `/page/…`
3. `browser_get_text` (and snapshot if navigating in-page)
4. Skip relying on on-site `/search` UI until it stops erroring
5. Cross-check facts against Wikipedia; Grokipedia pages are labeled "Fact-checked by Grok" with ages (e.g. 4–6 months) — useful second voice, not sole authority

### Unofficial clients (not tried this pass)
Grok mentioned PyPI `grokipedia-api` / community GitHub scrapers for structured full text. Not installed or verified here; browse + site-search was enough for reading.

---

## Grokipedia extracts (rockets / aero overlap)

### Rocket (grokipedia.com/page/Rocket)
- Self-contained reaction vehicle: onboard fuel **and** oxidizer → atmosphere + vacuum; Newton 3 via high-speed exhaust.
- Thrust sketch: **F = ṁ · v_e** (mass flow × exhaust velocity).
- History spine aligns with wiki pass: China fire arrows (Song / Kai-feng-fu 1232) → Tsiolkovsky 1903 liquid theory → Goddard 1926 (12.5 m) → V-2/von Braun → Sputnik 1957 → Apollo 11 1969.
- Classes: solid / liquid / hybrid chemical systems; applications from missiles to orbital launch and reusability push.
- Anatomy: airframe (stringers/hoops/skin), propellant tanks + pressurant (often He), engine + nozzle, payload fairing, guidance (IMU + thrust vector / fins).

### Tsiolkovsky rocket equation (grokipedia.com/page/Tsiolkovsky_rocket_equation)
- **Δv = v_e ln(m0/mf)** — ideal, no external forces; cornerstone of mission design.
- Momentum derivation: m dv = −v_e dm → integrate at constant v_e.
- "Tyranny of the rocket equation": ~**90%** mass propellant-class fractions for LEO-class Δv; drives staging + high-v_e propellants (e.g. LH2/LOX).
- Naming: Tsiolkovsky 1903 *Exploration of Cosmic Space by Means of Reaction Devices* (*Nauchnoye Obozreniye*); earlier/partial: William Moore 1813; parallel: Esnault-Pelterie, Goddard 1912, Oberth 1920.
- Thought experiment: closed carriage throwing mass rearward (momentum in isolation).
- Empirics: Goddard flights; V-2 / White Sands telemetry matched burnout velocity when adjusted for v_e ~2 km/s-class and mass ratio — deviations = drag + gravity losses (exactly what the ideal equation omits).
- Multistage called out as Tsiolkovsky's broader contribution ("space rocket trains" lineage).

### Rocket Lab (grokipedia.com/page/Rocket_Lab) — modern small-lift color
- Founded **2006** Peter Beck (NZ); HQ path Auckland → Long Beach, CA.
- **Electron**: electric-pump-fed **Rutherford** engines; small-lift LEO; **>70** successful orbital missions by Aug 2025 (page claim); rapid cadence (e.g. two missions &lt;48 h); also noted **four** failures historically (incl. 2023 ending a 20-success streak).
- Early: **Ātea-1** sounding rocket **2009-11-30** from Great Mercury Island — ~100–112 km, first NZ / first private Southern Hemisphere space reach.
- Electron debut "It's a Test" **2017-05-25** LC-1 Māhia — space but not orbit (ground-station comms).
- Sites: LC-1 Māhia; LC-2 Wallops (MARS) for US/gov cadence.
- **Photon** spacecraft; **Neutron** medium-lift reusable (Archimedes; fairing-first recovery concept); maiden targeted **late 2026** on this page — timeline skepticism + 2025 shareholder suit noted.
- Vertically integrated small-sat access story; national-security subsidiary / Geost acquisition / NASA & JAXA work.

### Airplane (grokipedia.com/page/Airplane)
- FAA-style def: engine-driven **fixed-wing** heavier-than-air craft supported primarily by dynamic reaction of air on wings (vs balloons / helicopters).
- Wrights **1903-12-17** Flyer I, Kitty Hawk — 120 ft / 12 s; built on Cayley's fixed-wing split of problems.
- Etymology: Fr. *aéroplane* ← Gk. *aēr* + *planos*; US standardized "airplane" (NACA 1916) vs Commonwealth "aeroplane."
- Categories: normal/utility/acrobatic/commuter/transport (FAA); commercial (Part 25) vs general aviation (Part 23) vs military roles (fighter/bomber/transport/ISR).
- Economic snapshot on page (US-centric): tens of thousands of flights/day; trillion-scale activity / millions of jobs — treat as page-dated stats, not re-verified here.

### Rocket-powered aircraft (grokipedia.com/page/Rocket-powered_aircraft) — bridge topic
- Fixed-wing + **only** rocket propulsion (stored oxidizer) → huge accel/speed/alt, short burns.
- Timeline highlights:
  - **1928-06-11** Fritz Stamer, Lippisch **Ente**, solid rocket ~1.5 km — early crewed rocket flight
  - **Me 163 Komet** (Lippisch; Walter HWK 509; service Jul 1944): ~596 mph, climb ~16,000 ft/min, ~7.5 min powered; hypergolic hazards; few kills
  - **Bell X-1** — Yeager **1947-10-14** Mach 1.06 @ 43k ft (first controlled supersonic)
  - **North American X-15** — XLR99 ~57k lbf; 199 flights 1959–68; **Mach 6.70**, alt **354,200 ft**; Shuttle-era data; 13 pilots earned astronaut wings
- Propulsion types same chemical triad (liquid / solid / hybrid); thrust form includes pressure term: F = ṁ v_e + (p_e − p_a) A_e
- Modern lineage page cites: SpaceShipOne, Virgin Galactic (as of 2025), Dawn Mk-II Aurora concepts — research/military still dominate vs routine transport
- **Contrast lock-in:** same rocket equation & oxidizer-onboard story as orbital rockets, but wings do the lift/glide after burnout — aeroplane airframe + rocket energy budget.

### Cross-check vs earlier Wikipedia pass
- No contradiction on fundamentals (reaction mass, Tsiolkovsky, staging tyranny, Wrights/Cayley, jet vs rocket oxidizer).
- Grokipedia added handy **modern ops color** (Rocket Lab cadence/Neutron) and a clean **rocket-plane bridge** article worth remembering beside pure LV and pure airliner notes.
- Prefer wiki for stable textbook physics; Grokipedia useful for alternate wording + newer program snapshots — still verify numbers before relying.

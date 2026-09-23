import json, sys, math, collections

MARKET_I0 = 10000
PRICE_FLOOR = 1
HINGE_GAIN = 8.0
MARKET_PARAMS = {
    "WHEAT":      {"base":  25, "I0": MARKET_I0, "T": 400, "below_func": "sqrt",   "below_target": 0.80, "above_func": "log",    "above_target": 0.20},
    "CARROT":     {"base":  35, "I0": MARKET_I0, "T": 450, "below_func": "hinge",  "below_target": 1.00, "above_func": "sqrt",   "above_target": 0.70},
    "TOMATO":     {"base":  60, "I0": MARKET_I0, "T": 200, "below_func": "hinge",  "below_target": 0.40, "above_func": "sqrt",   "above_target": 0.60},
    "STRAWBERRY": {"base": 120, "I0": MARKET_I0, "T": 100, "below_func": "sqrt",   "below_target": 0.70, "above_func": "linear", "above_target": 1.60},
    "MELON":      {"base": 250, "I0": MARKET_I0, "T": 300, "below_func": "log",    "below_target": 0.20, "above_func": "sq",     "above_target": 3.60},
    "EGG":        {"base":  50, "I0": MARKET_I0, "T": 332, "below_func": "hinge",  "below_target": 0.40, "above_func": "log",    "above_target": 0.20},
    "MILK":       {"base": 160, "I0": MARKET_I0, "T": 122, "below_func": "sqrt",   "below_target": 0.60, "above_func": "linear", "above_target": 1.60},
    "WOOL":       {"base": 200, "I0": MARKET_I0, "T": 105, "below_func": "log",    "below_target": 0.20, "above_func": "sq",     "above_target": 3.20},
    "FERTILIZER": {"base": 100, "I0": MARKET_I0, "T": 200, "below_func": "linear", "below_target": 0.40, "above_func": "linear", "above_target": 0.40},
}
CROPS_SEED = {"WHEAT":10,"CARROT":20,"TOMATO":50,"STRAWBERRY":100,"MELON":80}
ANIMAL_COST = {"GOOSE":300,"COW":400,"SHEEP":500}
SHOPS = {
    "BAKERY": ["EGG","WHEAT"], "PIZZA_SHOP": ["MILK","TOMATO","WHEAT"],
    "BRUNCH_SPOT": ["EGG","WHEAT","STRAWBERRY"], "YARN_STORE": ["WOOL"],
    "ICE_CREAM_SHOP": ["STRAWBERRY","MILK","WHEAT"], "PET_CAFE": ["CARROT"],
    "SMOOTHIE_SHOP": ["STRAWBERRY","MILK"], "FARMERS_MARKET": ["WHEAT","CARROT","TOMATO","STRAWBERRY"],
}
TOWN_CENTER_PRODUCTS = ["WHEAT","CARROT","TOMATO","STRAWBERRY","MELON","EGG","MILK","WOOL"]

def shape(func, x, T=None):
    x = max(0.0, x)
    if func=="linear": return x
    if func=="sq": return x*x
    if func=="sqrt": return math.sqrt(x)
    if func=="log": return math.log(1.0+x)
    if func=="log10": return math.log10(1.0+x)
    if func=="hinge":
        if not T or T<=0: return x
        u=x/T; return u+HINGE_GAIN*max(0.0,u-1.0)**2
    return x

def mprice(item, inv):
    p=MARKET_PARAMS[item]; base=p["base"]; I0=p["I0"]; T=p["T"]
    if inv<I0:
        amp=p["below_target"]*base/shape(p["below_func"],T,T)
        return max(PRICE_FLOOR,int(round(base+amp*shape(p["below_func"],I0-inv,T))))
    amp=p["above_target"]*base/shape(p["above_func"],T,T)
    return max(PRICE_FLOOR,int(round(base-amp*shape(p["above_func"],inv-I0,T))))

def run(path):
    d=json.load(open(path)); steps=d["steps"]; names=d["info"]["TeamNames"]
    P=max(range(len(names)), key=lambda i: d["rewards"][i])  # winner
    inv=dict(steps[0][0]["observation"]["market"]["inventory"])
    money_track=[collections.defaultdict(float) for _ in names]
    fills=[collections.defaultdict(lambda: [0,0.0]) for _ in names]  # item -> [units, net money]
    for t,slist in enumerate(steps):
        # build per-player order queues
        queues=[]
        for p in range(len(names)):
            a=slist[p]["action"] or {}
            queues.append(list(a.get("market") or [])[:10])
        # parse into (op,item,remaining) or atomic
        parsed=[]
        for q in queues:
            pq=[]
            for o in q:
                if o[0] in ("HIRE","BUY_LAND"): pq.append({"type":o[0]})
                elif o[0] in ("SELL","BUY_PRODUCT","BUY_SEED","BUY_ANIMAL") and len(o)>=3:
                    pq.append({"type":o[0],"item":o[1],"remaining":int(o[2])})
            parsed.append(pq)
        max_len=max((len(q) for q in parsed), default=0)
        for i in range(max_len):
            states=[q[i] if i<len(q) else None for q in parsed]
            # atomics first
            for p,o in enumerate(states):
                if o and o["type"] in ("HIRE","BUY_LAND"):
                    money_track[p][o["type"]]-= 1  # count only
                    states[p]=None
            # per-unit lockstep
            esc=0
            while True:
                esc+=1
                if esc>100000: break
                quoted=[None,None]
                for p,o in enumerate(states):
                    if o is None or o["remaining"]<=0: continue
                    op,it=o["type"],o["item"]
                    if op=="SELL" and it in MARKET_PARAMS:
                        quoted[p]=(op,it,mprice(it,inv[it]),o)
                    elif op=="BUY_PRODUCT" and it in ("WHEAT","FERTILIZER"):
                        quoted[p]=(op,it,mprice(it,inv[it]-1),o)
                    elif op=="BUY_SEED" and it in CROPS_SEED:
                        quoted[p]=(op,it,CROPS_SEED[it],o)
                    elif op=="BUY_ANIMAL" and it in ANIMAL_COST:
                        quoted[p]=(op,it,ANIMAL_COST[it],o)
                    else:
                        states[p]=None
                if all(q is None for q in quoted): break
                any_c=False
                for p,q in enumerate(quoted):
                    if q is None: continue
                    op,it,price,o=q
                    if op=="SELL":
                        money_track[p][it]+=price; fills[p][it][0]+=1; fills[p][it][1]+=price
                        if price>1: inv[it]+=1
                        o["remaining"]-=1; any_c=True
                    elif op in ("BUY_PRODUCT","BUY_SEED","BUY_ANIMAL"):
                        money_track[p][it]-=price; fills[p][it][0]-=1; fills[p][it][1]-=price
                        if op=="BUY_PRODUCT": inv[it]-=1
                        o["remaining"]-=1; any_c=True
                if not any_c: break
        # town consume after market (as in env)
        obs=slist[0]["observation"]
        shops=obs["town"]["unlocked_shops"]
        # NOTE: obs town state at step t is the state BEFORE this step's consume;
        # consumption at t uses shop list as of t (unlocks happen at end of day)
        if t%4==0:
            for sh in shops:
                prods=SHOPS[sh]; mult=2 if len(prods)==1 else 1
                for it in prods: inv[it]-=mult
        if t%24==0:
            for it in TOWN_CENTER_PRODUCTS: inv[it]-=1
    print("="*70)
    print(path)
    for p in range(len(names)):
        print(f"\nPlayer {p} {names[p]} (reward {d['rewards'][p]})")
        net=0
        for it,(u,m) in sorted(fills[p].items(), key=lambda x:-abs(x[1][1])):
            print(f"  {it:<10} units_delta={u:>6}  cash_flow={m:>10.0f}")
            net+=m
        hires=sum(-v for k,v in money_track[p].items() if k=="HIRE")
        lands=sum(-v for k,v in money_track[p].items() if k=="BUY_LAND")
        print(f"  HIRE orders: {int(hires)}  BUY_LAND orders: {int(lands)}")
        print(f"  NET market cash flow (excl hire/land/seeds counted above): {net:.0f}")
    # sanity: final inv vs replay final obs
    fo=steps[-1][0]["observation"]["market"]["inventory"]
    diff={k:(inv[k],fo[k]) for k in inv if inv[k]!=fo[k]}
    print("\nsim vs actual final inventory diffs:", diff if diff else "exact match")

for p in sys.argv[1:]:
    run(p)

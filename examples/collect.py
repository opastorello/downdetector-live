"""
Coletor completo: baixa todos os serviços de todos os países do Downdetector
e salva data/{iso2}.json + data/index.json.

Uso:  python collect.py <SpeedTestUserApiKey>
A key é a 'SpeedTestUserApiKey' do Info.plist do IPA do Speedtest.
"""
import json, subprocess, time, sys, os, uuid

UA = "SpeedTest/7.1.3.10 CFNetwork/1.0 Darwin/27.2.0"
F  = "id,stats_24,name,status,baseline,indicators,slug,description,logos"
CB = "https://consumer-downdetector-api.speedtest.net/v1"
ST = "https://st.downdetectorapi.com/v2"
OUT = os.path.join(os.path.dirname(__file__), "..", "data")

# capitais -> geolocaliza cada país (siteID vem do config)
CAPITALS = {
 "US":(40.71,-74),"GB":(51.5,-0.12),"DE":(52.5,13.4),"FR":(48.85,2.35),"ES":(40.4,-3.7),
 "IT":(41.9,12.5),"PT":(38.7,-9.14),"MX":(19.43,-99.13),"AR":(-34.6,-58.38),"JP":(35.68,139.69),
 "AU":(-33.87,151.2),"CA":(43.65,-79.38),"IN":(28.6,77.2),"BR":(-23.55,-46.63),"NL":(52.37,4.9),
 "BE":(50.85,4.35),"SE":(59.33,18.06),"NO":(59.91,10.75),"DK":(55.68,12.57),"FI":(60.17,24.94),
 "PL":(52.23,21.01),"RU":(55.75,37.62),"TR":(41.01,28.98),"NZ":(-36.85,174.76),"IE":(53.35,-6.26),
 "CH":(47.37,8.54),"AT":(48.21,16.37),"ID":(-6.2,106.8),"TH":(13.75,100.5),"MY":(3.14,101.69),
 "PH":(14.6,121.0),"KR":(37.57,126.98),"ZA":(-26.2,28.04),"CL":(-33.45,-70.67),"PE":(-12.05,-77.04),
}

def curl(args, t=40):
    return subprocess.run(["curl","-s","-m",str(t)]+args, capture_output=True, text=True).stdout

def token(key):
    u=str(uuid.uuid4())
    body=json.dumps({"device":{"id":u,"guid":u,"idfv":u,"platform":"ios","model":"iPhone15,2","version":"7.1.2"},"app":{"version":"7.1.2"}})
    return curl(["-X","POST","-H","X-Ookla-Api-Key: "+key,"-H","Content-Type: application/json","--data",body,CB+"/dd/config?latitude=-23.55&longitude=-46.63"])

def main(key):
    os.makedirs(OUT, exist_ok=True)
    AT=json.loads(token(key))["accessToken"]; t0=time.time()
    # mapeia iso2 -> siteID via config
    sites={}
    for iso,(lat,lng) in CAPITALS.items():
        r=token.__self__ if False else curl(["-X","POST","-H","X-Ookla-Api-Key: "+key,"-H","Content-Type: application/json",
            "--data",json.dumps({"device":{"id":str(uuid.uuid4()),"guid":str(uuid.uuid4()),"idfv":str(uuid.uuid4()),"platform":"ios","model":"iPhone15,2","version":"7.1.2"},"app":{"version":"7.1.2"}}),
            f"{CB}/dd/config?latitude={lat}&longitude={lng}"])
        try: ci=json.loads(r)["countryInfo"]; sites[iso]=(ci["siteID"],ci["name"])
        except: pass
    index=[]
    for iso,(sid,name) in sites.items():
        if time.time()-t0>600: AT=json.loads(token(key))["accessToken"]; t0=time.time()
        base=f"{ST}/sites/{sid}/companies?order_by=status&order_direction=desc&page_size=50&fields={F}"
        try: pages=json.loads(curl(["-X","OPTIONS","-H","Authorization: Bearer "+AT,"-A",UA,base])).get("pages",[])
        except: pages=[]
        comps={}
        for cur in pages:
            try:
                for c in json.loads(curl(["-H","Authorization: Bearer "+AT,"-A",UA,base+"&page="+cur])):
                    if isinstance(c,dict) and "id" in c: comps[c["id"]]=c
            except: pass
            time.sleep(0.15)
        arr=list(comps.values())
        json.dump(arr, open(f"{OUT}/{iso}.json","w"), ensure_ascii=False, separators=(",",":"))
        index.append({"iso2":iso,"siteID":sid,"domain":name,"count":len(arr)})
        print(f"{iso}: {len(arr)} serviços", flush=True)
    json.dump(index, open(f"{OUT}/index.json","w"), ensure_ascii=False, indent=1)
    print("TOTAL:", sum(x["count"] for x in index), "serviços em", len(index), "países")

if __name__=="__main__":
    if len(sys.argv)<2: sys.exit("uso: python collect.py <SpeedTestUserApiKey>")
    main(sys.argv[1])

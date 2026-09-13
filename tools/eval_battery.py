"""Extended run: 19 grounded questions x 3 repeats against /query."""
import json, sys, urllib.request, time
API="http://localhost:8000/query"; N=3
CASES=[
 ("mycheckr","What size screws do I need to mount the MyCheckr, and how many mounting holes are there?","4mm / 4 holes"),
 ("mycheckr","What options are available in Ads mode?","Gender/Screen filter, carousel"),
 ("mycheckr","I need to reset the API credentials, how do I do that?","Manage Device Login Details"),
 ("sku_scs","I need to mount a Twin SMART Coin System baseplate into our cabinet - how many screws does it take?","eight screws"),
 ("sku_scs","I need to clean the coin path in the feeder - how do I do that and how often?","mild detergent / no solvents"),
 ("sku_scs","What options are available for the pay-in chute on the SMART Coin System?","PA04138 Chute w/ Debris Collection"),
 ("sku_scs","Which settings does the SD card need to be formatted with?","MBR, FAT32, 4-32GB"),
 ("sku_scs","What size coins will the unit take - is there a minimum and maximum diameter?","18-28.5mm"),
 ("sku_scs","How much does the SMART Coin System weigh empty and when it's full?","4kg / ~18kg"),
 ("sku_scs","The status LED is flashing red four times - what does that mean?","Feeder Calibration Error"),
 ("sku_scs","Can I plug the USB straight into my host PC to talk to the machine?","No - IF17 TTL to USB"),
 ("nv9_spectral","Which pins on the 16-way connector do I have to wire for power?","pins 15 and 16"),
 ("nv9_spectral","What is the peak current draw of the NV9 Spectral?","2.5A @12V, 2A @24V"),
 ("nv9_spectral","My bezel is flashing one long flash then one short flash - what's wrong?","note path open"),
 ("nv9_spectral","How often should the belts be changed and the lenses cleaned?","belts 6-12mo, lenses 6mo"),
 ("nv9_spectral","What note sizes will the NV9 Spectral take?","115-167mm x 60-82mm"),
 ("nv9usb","Can I connect the NV9USB+ directly to an MDB vending machine?","No - IF5"),
 ("nv9usb","What cleaner is safe to use on the NV9USB+?","mild detergent only"),
 ("nv9_spectral","How do I put the validator into SSP programming mode?","hold config button >3s"),
]
def ask(p,q,sid):
    b={"q":q,"session_id":sid,"product":p,"skip_faq":True}
    r=urllib.request.Request(API,data=json.dumps(b).encode(),
        headers={"Content-Type":"application/json"})
    t0=time.time()
    try:
        with urllib.request.urlopen(r,timeout=300) as x: d=json.load(x)
    except Exception as e: d={"answer":"ERR %s"%e}
    d["_s"]=time.time()-t0; return d
def refused(a):
    a=a or ""
    return "don't have that in the product documentation" in a or a.startswith("ERR")
tot=ok=0; secs=0.0
for qi,(prod,q,exp) in enumerate(CASES,1):
    marks=[]
    for run in range(N):
        d=ask(prod,q,"ext-%d-%d-%s"%(qi,run,time.time()))
        a=(d.get("answer") or "").replace("\n"," ").strip()
        r=refused(a); marks.append("X" if r else ".")
        tot+=1; ok+= (not r); secs+=d["_s"]
        if run==0:
            first=a[:200]
    print("Q%-3d %-8s %-46s %s" % (qi,"".join(marks),q[:46],""))
    print("       expect: %s" % exp)
    print("       got   : %s" % first)
    sys.stdout.flush()
print("\nANSWERED %d/%d (%.0f%%)   avg %.1fs/call" % (ok,tot,100.0*ok/tot,secs/tot))

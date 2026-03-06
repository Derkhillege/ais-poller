
from websockets.sync.client import connect
from dotenv import load_dotenv
import os
import json
import time
import datetime

def main():
    load_dotenv()
    euris_wss = os.getenv("EURIS_WSS")
    request_params = json.loads(os.getenv("REQUEST_PARAMS"))
    
    with connect(euris_wss) as websocket:
        while True:
            websocket.send(json.dumps(request_params))
            response = websocket.recv()
            request_timeish = datetime.datetime.now().isoformat()               
            with open("data.json", "a") as f:
                for record in json.loads(response)["result"]["features"]:
                    record["requested_at"] = request_timeish
                    json.dump(record, f)
                    f.write("\n")
            request_params["id"] += 1
            time.sleep(int(os.getenv("INTERVAL")))

if __name__ == "__main__":
    main()

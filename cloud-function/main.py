import os
from bot import main

def nifty50_bot(request):
    try:
        os.environ["ZERODHA_ENCTOKEN"] = "c4L5w7xFCuLnjof+5jlRWE0KUj7FKu/HjaAwG28wDmouZWO2NXkdfeI5iI92XJASPRO+agse5XbVc61eO0bqDFIlpQQSgANedDk8nXrhqhTa80tdreRT9A=="
        main()
        return {"status": "success"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

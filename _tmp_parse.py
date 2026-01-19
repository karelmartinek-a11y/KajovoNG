import json, pathlib
p=pathlib.Path(r"LOG\RUN_100120261805_IQO0\responses\Kájovo_RUN_100120261805_IQO0_A3_FILE_resp_0a572ade9fcd52ab0069629f25647c8191abbd27b662fd2d1c_app_ai_dagmar_personality_engine.py_0_100120261952.json")
text=json.loads(p.read_text(encoding='utf-8'))['output'][0]['content'][0]['text']
try:
    json.loads(text)
    print('ok')
except Exception as e:
    print(e)
    idx=getattr(e,'pos',None)
    if idx is None and hasattr(e,'args') and e.args:
        try:
            idx=e.args[2]
        except Exception:
            idx=None
    if idx is not None:
        start=max(0,idx-40); end=min(len(text), idx+80)
        print('around error:', text[start:end])

import sys
import asyncio

import tests


def test(token, jupyter_url):
    print("""
Jupyter Kernel WebSocket Client - Test Suite
============================================

""")
    
        
    # Uncomment the test you want to run:
    asyncio.run(tests.test_basic_execution(token, jupyter_url))
    # asyncio.run(test_ollama_setup())
    # asyncio.run(test_json_payload())


def main():
    if len(sys.argv) < 2 or len(sys.argv) > 3:
        print(f"Usage: {sys.argv[0]} TOKEN [JUPYTER_URL]")
        if len(sys.argv) < 2:
            print("""
To get your token:
- Run in a Jupyter notebook: 
  import os; print(os.environ.get('JUPYTERHUB_API_TOKEN'))
- Or generate at: https://your-jupyterhub/hub/token
- Or press ^C in jupyter server stdout
""")
        sys.exit(1)

    token = sys.argv[1]
    if len(sys.argv) == 3:
        jupyter_url = sys.argv[2]
    else:
        jupyter_url = 'http://localhost:8888'



    # COMMANDS
    test(token, jupyter_url)


if __name__ == "__main__":
    main()

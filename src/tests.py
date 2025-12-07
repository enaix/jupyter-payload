import client


# Example usage and test cases
async def test_basic_execution(token, jupyter_url):
    """Test basic code execution"""
    print("\n" + "="*60)
    print("TEST 1: Basic Execution")
    print("="*60)
    
    cli = client.JupyterKernelClient(
        jupyter_url,
        token
    )
    
    try:
        cli.create_session()
        await cli.connect_websocket()
        
        # Test 1: Simple print
        res = await cli.execute_and_get_output('print("Hello from Jupyter kernel!")', verbose=True)
        print('\"', res.strip(), '\"', sep="")
        assert(res.strip() == "Hello from Jupyter kernel!")
        
        # Test 2: Variable assignment and return
        res = await cli.execute_and_get_output('''
x = 42
y = x * 2
y
''', verbose=True)
        print('\"', res.strip(), '\"', sep="")
        assert(res.strip() == str(42*2))
        
        # Test 3: Import and use library
        res = await cli.execute_and_get_output('''
import math
result = math.sqrt(144)
print(f"Square root of 144 is {result}")
''', verbose=True)
        print('\"', res.strip(), '\"', sep="")
        assert(res.strip() == "Square root of 144 is 12.0")
        
    finally:
        await cli.close()


async def test_http_request(token, jupyter_ur):
    """Test HTTP request function"""
    print("\n" + "="*60)
    print("TEST 2: HTTP Request via Kernel")
    print("="*60)

    client = JupyterKernelClient(
        jupyter_url,
        token
    )

    try:
        client.create_session()
        await client.connect_websocket()

        # First ensure Ollama is running in the kernel
        print("\n1. Starting http server in kernel...")
        setup_code = '''
from http.server import BaseHTTPRequestHandler, HTTPServer
host = "0.0.0.0"
port = 12333

class MyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(bytes("response from jupyter", "utf-8"))

server = HTTPServer((host, port), MyServer)
server.handle_request()  # serve and die
'''
        await client.execute_code(setup_code, verbose=False)

        # Test 1: GET request
        print("\n2. Testing GET request to /...")
        result = await client.http_request('GET', '127.0.0.1:12333/')
        print(f"   Status: {result['status_code']}")
        print(f"   Response: {result['body'][:100]}...")
        assert(result['body'] == "response from jupyter")
        print("Test passed")


    finally:
        await client.close()

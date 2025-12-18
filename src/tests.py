import asyncio

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
    except AssertionError:
        raise e
    finally:
        await cli.close()


async def test_http_request(token, jupyter_url):
    """Test HTTP request function"""
    print("\n" + "="*60)
    print("TEST 2: HTTP Request via Kernel")
    print("="*60)

    cli = client.JupyterKernelClient(
        jupyter_url,
        token
    )

    try:
        cli.create_session()
        await cli.connect_websocket()

        # First ensure Ollama is running in the kernel
        print("\n1. Starting http server in kernel...")
        setup_code = '''
from threading import Thread
from http.server import BaseHTTPRequestHandler, HTTPServer
host = "0.0.0.0"
port = 12333

class MyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.end_headers()
        self.wfile.write(bytes("response from jupyter", "utf-8"))

    def log_message(self, format, *args):
        return

def serve():
    server = HTTPServer((host, port), MyServer)
    server.handle_request()  # serve and die

thread = Thread(target=serve)
thread.start() # The thread will die after we execute a request
'''
        # No await
        server_msg, _ = await cli.execute_code_nowait(setup_code, verbose=False)

        # Test 1: GET request
        # TODO understand why stdouts of different messages somehow get mixed
        print("\n2. Testing GET request to the server...")
        result = await cli.http_request('GET', 'http://127.0.0.1:12333/', verbose=True)
        print(result)
        print(f"   Status: {result['status_code']}")
        print(f"   Response: {result['body'][:100]}...")
        assert(result['body'] == "response from jupyter")
        print("Test passed")
    except AssertionError:
        raise e
    finally:
        await cli.close()


async def test_stdin_interaction(token, jupyter_url):
    """Test stdin interaction with kernel"""
    print("\n" + "="*60)
    print("TEST 3: Stdin Interaction")
    print("="*60)

    cli = client.JupyterKernelClient(
        jupyter_url,
        token
    )

    try:
        cli.create_session()
        await cli.connect_websocket()

        print("\n1. Executing code that requests stdin...")

        # Code that immediately requests input and echoes 3 times
        stdin_code = '''
for i in range(3):
    user_input = input(f"Enter text (attempt {i+1}/3): ")
    print(user_input)
print("Done!")
'''

        # Execute without waiting for completion
        msg_id, msg_header = await cli.execute_code_nowait(stdin_code, verbose=True, stdin=True)

        print("\n2. Handling stdin requests and collecting outputs...")

        outputs = []
        error = None
        status = 'unknown'
        stdin_count = 0
        timeout_seconds = 60
        sent_output = ""

        try:
            async with asyncio.timeout(timeout_seconds):
                got_exec_reply = False

                while not got_exec_reply:
                    msg = await cli.read_message(timeout_seconds, verbose=True)

                    # Only process messages related to our execution
                    if msg.get('msg_id') != msg_id:
                        continue

                    msg_type = msg.get('msg_type')

                    # Handle stdin request
                    if msg_type == 'input_request':
                        prompt = msg['_msg']['content'].get('prompt', '')
                        print(f"    Stdin request #{stdin_count}: {prompt}")

                        stdin_count += 1
                        # Send input reply
                        input_value = f"Test input #{stdin_count}\n"
                        await cli.send_message(cli.new_msg_id(), 'input_reply', {'value': input_value}, msg['_msg']['header'], channel='stdin')
                        print(f"    >>> Sent: {input_value}")
                        sent_output += input_value + '\n'

                    else:
                        # Handle outputs already processed by read_message
                        if msg.get('output') is not None:
                            outputs.append(msg['output'])

                        # Handle errors
                        if msg.get('error') is not None:
                            error = msg['error']

                        # Handle execution completion
                        if msg.get('status') is not None:
                            status = msg['status']
                            got_exec_reply = True

        except asyncio.TimeoutError:
            print(f"\n    Execution timed out after {timeout_seconds}s")
            status = 'timeout'

        sent_output += "Done!\n"
        output_str = cli.glue_output(outputs)
        # Verify results
        print("\n3. Verifying results...")
        print(f"   Status: {status}")
        print(f"   Stdin requests handled: {stdin_count}")
        print(f"   Outputs collected: {output_str}")

        assert output_str == sent_output, "Echoed stdout differs from sent stdout"

    finally:
        await cli.close()

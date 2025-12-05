import client


# Example usage and test cases
async def test_basic_execution(token, jupyter_url='http://localhost:8888'):
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
        await cli.execute_code('print("Hello from Jupyter kernel!")')
        
        # Test 2: Variable assignment and return
        await cli.execute_code('''
x = 42
y = x * 2
y
''')
        
        # Test 3: Import and use library
        await cli.execute_code('''
import math
result = math.sqrt(144)
print(f"Square root of 144 is {result}")
''')
        
    finally:
        await cli.close()


async def test_ollama_setup():
    """Test Ollama initialization"""
    print("\n" + "="*60)
    print("TEST 2: Ollama Setup")
    print("="*60)
    
    cli = client.JupyterKernelClient(
        jupyter_url='http://localhost:8888',
        token='your-token-here'
    )
    
    try:
        cli.create_session()
        await cli.connect_websocket()
        
        # Initialize Ollama
        setup_code = '''
import subprocess
import requests
import json

# Start Ollama
ollama_process = subprocess.Popen(
    ['ollama', 'serve'], 
    stdout=subprocess.PIPE, 
    stderr=subprocess.PIPE
)

print("Ollama process started")

def ollama_request(endpoint, data):
    try:
        resp = requests.post(
            f'http://localhost:11434{endpoint}',
            json=data,
            timeout=300
        )
        return resp.json()
    except Exception as e:
        return {'error': str(e)}

print("Helper function defined")
'''
        
        await cli.execute_code(setup_code)
        
        # Test Ollama is running
        test_code = '''
import time
time.sleep(5)  # Give Ollama time to start
result = ollama_request('/api/tags', {})
print(f"Available models: {result}")
'''
        
        await cli.execute_code(test_code)
        
    finally:
        await cli.close()


async def test_json_payload():
    """Test sending and receiving JSON payloads"""
    print("\n" + "="*60)
    print("TEST 3: JSON Payload Handling")
    print("="*60)
    
    cli = JupyterKernelClient(
        jupyter_url='http://localhost:8888',
        token='your-token-here'
    )
    
    try:
        cli.create_session()
        await cli.connect_websocket()
        
        # Send JSON payload and get JSON response
        code = '''
import json

input_data = {
    "model": "llama2",
    "prompt": "Say hello",
    "stream": False
}

print(json.dumps({
    "received": input_data,
    "status": "processing"
}))
'''
        
        result = await cli.execute_and_get_output(code, output_type='text')
        print(f"\nReceived JSON: {result}")
        
    finally:
        await cli.close()



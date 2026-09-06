import socket

def run_server():
    # 1. Create a socket object (AF_INET = IPv4, SOCK_STREAM = TCP)
    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    # Allow restarting the server immediately without "address already in use" errors
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    # 2. Bind the socket to a host and port ('127.0.0.1' is localhost)
    host = '127.0.0.1'
    port = 65432
    server_socket.bind((host, port))

    # 3. Listen for incoming connections (allow up to 5 queued connections)
    server_socket.listen(5)
    print(f"Server is listening on {host}:{port}...")

    # 4. Accept a connection (blocks until a client connects)
    client_socket, client_address = server_socket.accept()
    print(f"Connected to client: {client_address}")

    while True:
        # 5. Receive data from the client (buffer size up to 1024 bytes)
        data = client_socket.recv(1024)
        if not data:
            # If no data is received, the client disconnected
            break

        print(f"Received from client: {data.decode('utf-8')}")

        # 6. Echo the data back to the client
        client_socket.sendall(data)

    # 7. Clean up the connections
    client_socket.close()
    server_socket.close()
    print("Server shut down.")

if __name__ == '__main__':
    run_server()
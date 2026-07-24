import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    client.connect('100.89.204.21', username='cmos', password='pesgroup@2025', timeout=10, allow_agent=False, look_for_keys=False)
    stdin, stdout, stderr = client.exec_command('ls -la')
    print("--- Files in Home Directory ---")
    print(stdout.read().decode())
    
    stdin, stdout, stderr = client.exec_command('find . -name "*.v"')
    print("--- Verilog files ---")
    print(stdout.read().decode())
finally:
    client.close()

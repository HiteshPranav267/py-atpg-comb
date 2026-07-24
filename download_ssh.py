import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    client.connect('100.89.204.21', username='cmos', password='pesgroup@2025', timeout=10, allow_agent=False, look_for_keys=False)
    sftp = client.open_sftp()
    
    # Download s1196 netlists
    remote_path_1 = './s1196_capstone/test_scripts/s1196.test_netlist.v'
    local_path_1 = 'benchmarks/s1196_scan.v'
    sftp.get(remote_path_1, local_path_1)
    
    remote_path_2 = './s1238_capstone/test_scripts/s1238.test_netlist.v'
    local_path_2 = 'benchmarks/s1238_scan.v'
    sftp.get(remote_path_2, local_path_2)

    print("Successfully downloaded s1196_scan.v and s1238_scan.v")
finally:
    sftp.close()
    client.close()

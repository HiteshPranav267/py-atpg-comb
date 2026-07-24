import paramiko

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
try:
    client.connect('100.89.204.21', username='cmos', password='pesgroup@2025', timeout=10, allow_agent=False, look_for_keys=False)
    sftp = client.open_sftp()
    
    # Try downloading from aa35932
    paths = [
        './aa35932/test_scripts/s35932.test_netlist.v',
        './aa35932/s35932_mod.v'
    ]
    for p in paths:
        try:
            local_p = 'benchmarks/' + p.split('/')[-1]
            sftp.get(p, local_p)
            print(f"Downloaded {p}")
        except Exception as e:
            print(f"Failed to download {p}: {e}")

finally:
    sftp.close()
    client.close()

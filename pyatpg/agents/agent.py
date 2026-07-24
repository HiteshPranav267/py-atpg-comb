"""
PyATPG Remote Agent Module
Automates the SSH/SFTP workflow between local machine and college server.
"""

import paramiko
import os
import posixpath

class RemoteAgent:
    """Agent that bridges local PyATPG with the remote Cadence server."""

    def __init__(self, host='pescad-14.pes.edu', username=None, password=None):
        self.host = host
        self.username = username
        self.password = password
        self.ssh = None

    def connect(self):
        """Establish SSH connection."""
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self.ssh.connect(self.host, username=self.username, password=self.password)
            return True, "Connected successfully"
        except Exception as e:
            return False, str(e)

    def upload_zip(self, local_path, remote_dir='/home/cmos/pyatpg_runs/'):
        """Upload job bundle and unzip it on server."""
        if not self.ssh:
            return False, "Not connected"

        sftp = self.ssh.open_sftp()
        try:
            # Create remote dir if not exists
            self.ssh.exec_command(f'mkdir -p {remote_dir}')
            
            filename = os.path.basename(local_path)
            remote_path = os.path.join(remote_dir, filename)
            
            sftp.put(local_path, remote_path)
            
            # Unzip it
            folder_name = filename.replace('.zip', '')
            unzip_dir = os.path.join(remote_dir, folder_name)
            self.ssh.exec_command(f'mkdir -p {unzip_dir} && unzip -o {remote_path} -d {unzip_dir}')
            
            return True, unzip_dir
        except Exception as e:
            return False, str(e)
        finally:
            sftp.close()

    def run_job(self, remote_job_dir):
        """Run the generated csh automation script remotely."""
        if not self.ssh:
            return False, "Not connected"

        full_command = f"cd {remote_job_dir} && chmod +x run_all.csh run_xcelium.sh && csh run_all.csh"
        
        stdin, stdout, stderr = self.ssh.exec_command(full_command)
        
        # Capture real-time output
        out_text = stdout.read().decode('utf-8')
        err_text = stderr.read().decode('utf-8')
        
        return True, out_text + "\n" + err_text

    def download_results(self, remote_job_dir, local_results_dir):
        """Download report files back to local results folder."""
        if not self.ssh:
            return False, "Not connected"

        if not os.path.exists(local_results_dir):
            os.makedirs(local_results_dir)

        sftp = self.ssh.open_sftp()
        try:
            downloaded = []

            def should_download(name):
                return name.endswith(('.rpt', '.log', '.v', '.stil', '.tcl', '.flt', '.json'))

            def walk(remote_dir, local_dir):
                os.makedirs(local_dir, exist_ok=True)
                for entry in sftp.listdir_attr(remote_dir):
                    remote_path = posixpath.join(remote_dir, entry.filename)
                    local_path = os.path.join(local_dir, entry.filename)
                    if entry.st_mode & 0o040000:
                        walk(remote_path, local_path)
                    elif should_download(entry.filename):
                        sftp.get(remote_path, local_path)
                        downloaded.append(remote_path)

            walk(remote_job_dir, local_results_dir)
            
            return True, downloaded
        except Exception as e:
            return False, str(e)
        finally:
            sftp.close()

    def disconnect(self):
        """Close connection."""
        if self.ssh:
            self.ssh.close()

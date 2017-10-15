from flask import Flask
import json

class RiotServer(object):

    @staticmethod
    def hello():
        return "hello world"


    def update(self):
        records = self.graphs.push_refresh()
        if records != None:
            status = {'status': 'ok', 'updates': records}
        else:
            status = {'status': 'error'}
        return json.dumps(status)

    def __init__(self, args, graphs):
        self.args = args
        self.graphs = graphs
        self.app = Flask(__name__)
        self.app.add_url_rule('/', 'index', RiotServer.hello)
        self.app.add_url_rule('/update', 'update', self.update)

    def run(self):
        self.app.run(host='::1', port=8080, debug=True)

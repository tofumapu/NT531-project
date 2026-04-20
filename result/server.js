var express = require('express'),
    async = require('async'),
    { Pool } = require('pg'),
    cookieParser = require('cookie-parser'),
    promClient = require('prom-client'),
    path = require('path'),
    app = express(),
    server = require('http').Server(app),
    io = require('socket.io')(server);

var port = process.env.PORT || 4000;
var register = new promClient.Registry();

promClient.collectDefaultMetrics({
  prefix: 'result_',
  register: register
});

var httpRequestCount = new promClient.Counter({
  name: 'result_http_requests_total',
  help: 'Total HTTP requests handled by the result service',
  labelNames: ['method', 'route', 'status_code'],
  registers: [register]
});

var httpRequestDuration = new promClient.Histogram({
  name: 'result_http_request_duration_seconds',
  help: 'HTTP request duration for the result service',
  labelNames: ['method', 'route', 'status_code'],
  registers: [register],
  buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5]
});

var dbPollCount = new promClient.Counter({
  name: 'result_db_polls_total',
  help: 'Total result refresh polls against PostgreSQL',
  labelNames: ['status'],
  registers: [register]
});

var dbPollDuration = new promClient.Histogram({
  name: 'result_db_poll_duration_seconds',
  help: 'Duration of result refresh polls against PostgreSQL',
  registers: [register],
  buckets: [0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1]
});

var socketConnections = new promClient.Counter({
  name: 'result_socket_connections_total',
  help: 'Total Socket.IO client connections',
  registers: [register]
});

io.on('connection', function (socket) {
  socketConnections.inc();

  socket.emit('message', { text : 'Welcome!' });

  socket.on('subscribe', function (data) {
    socket.join(data.channel);
  });
});

var pool = new Pool({
  connectionString: 'postgres://postgres:postgres@db/postgres'
});

async.retry(
  {times: 1000, interval: 1000},
  function(callback) {
    pool.connect(function(err, client, done) {
      if (err) {
        console.error("Waiting for db");
      }
      callback(err, client);
    });
  },
  function(err, client) {
    if (err) {
      return console.error("Giving up");
    }
    console.log("Connected to db");
    getVotes(client);
  }
);

function getVotes(client) {
  var endTimer = dbPollDuration.startTimer();
  client.query('SELECT vote, COUNT(id) AS count FROM votes GROUP BY vote', [], function(err, result) {
    if (err) {
      dbPollCount.inc({ status: 'error' });
      endTimer();
      console.error("Error performing query: " + err);
    } else {
      dbPollCount.inc({ status: 'success' });
      endTimer();
      var votes = collectVotesFromResult(result);
      io.sockets.emit("scores", JSON.stringify(votes));
    }

    setTimeout(function() {getVotes(client) }, 1000);
  });
}

function collectVotesFromResult(result) {
  var votes = {a: 0, b: 0};

  result.rows.forEach(function (row) {
    votes[row.vote] = parseInt(row.count);
  });

  return votes;
}

app.use(cookieParser());
app.use(express.urlencoded({ extended: false }));

app.use(function (req, res, next) {
  var route = req.path;
  var endTimer = httpRequestDuration.startTimer({
    method: req.method,
    route: route
  });

  res.on('finish', function () {
    var labels = {
      method: req.method,
      route: route,
      status_code: String(res.statusCode)
    };

    httpRequestCount.inc(labels);
    endTimer(labels);
  });

  next();
});

app.get('/metrics', async function (req, res) {
  res.set('Content-Type', register.contentType);
  res.end(await register.metrics());
});

app.use(express.static(__dirname + '/views'));

app.get('/', function (req, res) {
  res.sendFile(path.resolve(__dirname + '/views/index.html'));
});

server.listen(port, function () {
  var port = server.address().port;
  console.log('App running on port ' + port);
});

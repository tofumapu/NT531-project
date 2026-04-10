using System;
using System.Data.Common;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Threading;
using Newtonsoft.Json;
using Npgsql;
using Prometheus;
using StackExchange.Redis;

namespace Worker
{
    public class Program
    {
        private static readonly Counter ProcessedVotes = Metrics
            .CreateCounter("worker_processed_votes_total", "Number of votes processed by the worker.", new CounterConfiguration
            {
                LabelNames = new[] { "vote" }
            });

        private static readonly Counter ProcessingErrors = Metrics
            .CreateCounter("worker_processing_errors_total", "Number of worker processing errors.");

        private static readonly Counter RedisReconnects = Metrics
            .CreateCounter("worker_redis_reconnect_total", "Number of times the worker reconnects to Redis.");

        private static readonly Counter DbReconnects = Metrics
            .CreateCounter("worker_db_reconnect_total", "Number of times the worker reconnects to PostgreSQL.");

        private static readonly Gauge RedisConnected = Metrics
            .CreateGauge("worker_redis_connected", "Whether the worker currently considers Redis connected.");

        private static readonly Gauge DbConnected = Metrics
            .CreateGauge("worker_db_connected", "Whether the worker currently considers PostgreSQL connected.");

        private static readonly Gauge LastVoteProcessedTimestamp = Metrics
            .CreateGauge("worker_last_vote_processed_unixtime", "Unix timestamp of the last processed vote.");

        private static readonly Histogram VoteProcessingDuration = Metrics
            .CreateHistogram("worker_vote_processing_duration_seconds", "Time spent processing a vote in the worker.", new HistogramConfiguration
            {
                Buckets = Histogram.ExponentialBuckets(0.001, 2, 12)
            });

        public static int Main(string[] args)
        {
            try
            {
                var metricServer = new MetricServer(port: 9090);
                metricServer.Start();
                Console.WriteLine("Worker metrics server listening on :9090/metrics");

                var pgsql = OpenDbConnection("Server=db;Username=postgres;Password=postgres;");
                var redisConn = OpenRedisConnection("redis");
                var redis = redisConn.GetDatabase();

                // Keep alive is not implemented in Npgsql yet. This workaround was recommended:
                // https://github.com/npgsql/npgsql/issues/1214#issuecomment-235828359
                var keepAliveCommand = CreateKeepAliveCommand(pgsql);

                var definition = new { vote = "", voter_id = "" };
                while (true)
                {
                    // Slow down to prevent CPU spike, only query each 100ms
                    Thread.Sleep(100);

                    // Reconnect redis if down
                    if (redisConn == null || !redisConn.IsConnected) {
                        Console.WriteLine("Reconnecting Redis");
                        RedisReconnects.Inc();
                        redisConn = OpenRedisConnection("redis");
                        redis = redisConn.GetDatabase();
                    }

                    RedisConnected.Set(redisConn != null && redisConn.IsConnected ? 1 : 0);
                    DbConnected.Set(pgsql.State.Equals(System.Data.ConnectionState.Open) ? 1 : 0);

                    string json = redis.ListLeftPopAsync("votes").Result;
                    if (json != null)
                    {
                        using (VoteProcessingDuration.NewTimer())
                        {
                            var vote = JsonConvert.DeserializeAnonymousType(json, definition);
                            Console.WriteLine($"Processing vote for '{vote.vote}' by '{vote.voter_id}'");
                            // Reconnect DB if down
                            if (!pgsql.State.Equals(System.Data.ConnectionState.Open))
                            {
                                Console.WriteLine("Reconnecting DB");
                                DbReconnects.Inc();
                                keepAliveCommand.Dispose();
                                pgsql.Dispose();
                                pgsql = OpenDbConnection("Server=db;Username=postgres;Password=postgres;");
                                keepAliveCommand = CreateKeepAliveCommand(pgsql);
                            }

                            UpdateVote(pgsql, vote.voter_id, vote.vote);
                            ProcessedVotes.WithLabels(vote.vote ?? "unknown").Inc();
                            LastVoteProcessedTimestamp.Set(DateTimeOffset.UtcNow.ToUnixTimeSeconds());
                        }
                    }
                    else
                    {
                        if (!pgsql.State.Equals(System.Data.ConnectionState.Open))
                        {
                            Console.WriteLine("Reconnecting DB");
                            DbReconnects.Inc();
                            keepAliveCommand.Dispose();
                            pgsql.Dispose();
                            pgsql = OpenDbConnection("Server=db;Username=postgres;Password=postgres;");
                            keepAliveCommand = CreateKeepAliveCommand(pgsql);
                        }
                        else
                        {
                            keepAliveCommand.ExecuteNonQuery();
                        }
                    }
                }
            }
            catch (Exception ex)
            {
                ProcessingErrors.Inc();
                Console.Error.WriteLine(ex.ToString());
                return 1;
            }
        }

        private static NpgsqlConnection OpenDbConnection(string connectionString)
        {
            NpgsqlConnection connection;

            while (true)
            {
                try
                {
                    connection = new NpgsqlConnection(connectionString);
                    connection.Open();
                    break;
                }
                catch (SocketException)
                {
                    Console.Error.WriteLine("Waiting for db");
                    Thread.Sleep(1000);
                }
                catch (DbException)
                {
                    Console.Error.WriteLine("Waiting for db");
                    Thread.Sleep(1000);
                }
            }

            Console.Error.WriteLine("Connected to db");
            DbConnected.Set(1);

            var command = connection.CreateCommand();
            command.CommandText = @"CREATE TABLE IF NOT EXISTS votes (
                                        id VARCHAR(255) NOT NULL UNIQUE,
                                        vote VARCHAR(255) NOT NULL
                                    )";
            command.ExecuteNonQuery();

            return connection;
        }

        private static NpgsqlCommand CreateKeepAliveCommand(NpgsqlConnection connection)
        {
            var command = connection.CreateCommand();
            command.CommandText = "SELECT 1";
            return command;
        }

        private static ConnectionMultiplexer OpenRedisConnection(string hostname)
        {
            // Use IP address to workaround https://github.com/StackExchange/StackExchange.Redis/issues/410
            var ipAddress = GetIp(hostname);
            Console.WriteLine($"Found redis at {ipAddress}");

            while (true)
            {
                try
                {
                    Console.Error.WriteLine("Connecting to redis");
                    var redis = ConnectionMultiplexer.Connect(ipAddress);
                    RedisConnected.Set(redis.IsConnected ? 1 : 0);
                    return redis;
                }
                catch (RedisConnectionException)
                {
                    RedisConnected.Set(0);
                    Console.Error.WriteLine("Waiting for redis");
                    Thread.Sleep(1000);
                }
            }
        }

        private static string GetIp(string hostname)
            => Dns.GetHostEntryAsync(hostname)
                .Result
                .AddressList
                .First(a => a.AddressFamily == AddressFamily.InterNetwork)
                .ToString();

        private static void UpdateVote(NpgsqlConnection connection, string voterId, string vote)
        {
            var command = connection.CreateCommand();
            try
            {
                command.CommandText = "INSERT INTO votes (id, vote) VALUES (@id, @vote)";
                command.Parameters.AddWithValue("@id", voterId);
                command.Parameters.AddWithValue("@vote", vote);
                command.ExecuteNonQuery();
            }
            catch (DbException)
            {
                command.CommandText = "UPDATE votes SET vote = @vote WHERE id = @id";
                command.ExecuteNonQuery();
            }
            catch
            {
                ProcessingErrors.Inc();
                throw;
            }
            finally
            {
                command.Dispose();
            }
        }
    }
}

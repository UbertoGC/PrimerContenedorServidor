const express = require("express");
const mysql = require("mysql2/promise");
const cors = require("cors");

const app = express();
const port = 5000;

app.use(cors({
  origin: "http://localhost:8080"
}));

app.use(express.json());

const pool = mysql.createPool({
  host: process.env.DB_HOST || "localhost",
  user: process.env.DB_USER || "usuario",
  password: process.env.DB_PASSWORD || "clave",
  database: process.env.DB_NAME || "miapp"
});

app.post("/registro", async (req, res) => {
  const { usuario, contra } = req.body;
  try {
    await pool.query("INSERT INTO usuarios (usuario, contra) VALUES (?, ?)", [usuario, contra]);
    res.json({ ok: true, msg: "Usuario registrado" });
  } catch (err) {
    console.error(err);
    res.status(500).json({ ok: false, msg: "Error al registrar usuario" });
  }
});

app.get("/usuarios", async (req, res) => {
  try {
    const [rows] = await pool.query("SELECT * FROM usuarios");
    res.json(rows);
  } catch (err) {
    console.error(err);
    res.status(500).json({ ok: false, msg: "Error en la base de datos" });
  }
});
app.listen(port, "0.0.0.0", () => {
  console.log(`Servidor backend en puerto ${port}`);
});